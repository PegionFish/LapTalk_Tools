#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

<#
用途：
1. 在 Windows 11 OOBE 阶段创建本地管理员账户 LapTalk
2. 挂载 NAS 的 SMB 共享为网络磁盘
3. 暂停 Windows 更新活动

适用场景：
- 在 OOBE 画面按 Shift + F10 打开命令行
- 通过 U 盘运行本脚本
- 头像文件与本脚本放在同一文件夹中

运行示例：
powershell -ExecutionPolicy Bypass -File E:\LapTalk-OOBE-Setup.ps1

请先修改下面“需要手动填写”的配置项。
#>

# ============================================================
# 需要手动填写 / 修改的配置项
# ============================================================

$Config = @{
    LocalUserName      = 'LapTalk'
    LocalPasswordPlain = ''
    # 说明：
    # - 建议改成强密码，例如：P@ssw0rd-ChangeMe-2026
    # - 如果留空，将创建“无密码”本地管理员账户
    # - 出于安全考虑，推荐你部署完成后立刻改为强密码

    AvatarFileName     = ''
    # 说明：
    # - 这里只写“文件名”，不要写完整路径
    # - 头像文件默认与本脚本放在同一文件夹
    # - 示例：LapTalk.jpg
    # - 建议使用 JPG / JPEG / PNG

    NasRemotePath      = ''
    # 说明：
    # - 必须写成 \\服务器\共享名
    # - 不能只写 IP
    # - 正确示例：\\192.168.1.20\Public
    # - 错误示例：192.168.1.20

    NasDriveLetter     = 'Z:'
    # 说明：
    # - 期望挂载成哪个盘符，默认 Z:

    NasUserName        = ''
    NasPasswordPlain   = ''
    # 说明：
    # - 如果 NAS 允许匿名访问，这两项可以都留空
    # - 如果 NAS 需要账号密码，这两项必须同时填写
    # - 用户名示例：nasuser 或 192.168.1.20\nasuser
}

# ============================================================
# 工具函数
# ============================================================

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkGray
    Write-Host $Message -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkGray
}

function Write-Info {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[信息] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([Parameter(Mandatory)][string]$Message)
    Write-Warning $Message
}

function Normalize-DriveLetter {
    param([Parameter(Mandatory)][string]$DriveLetter)

    $value = $DriveLetter.Trim().ToUpper()
    if ($value.Length -eq 1) {
        $value = "${value}:"
    }

    if ($value -notmatch '^[A-Z]:$') {
        throw "NasDriveLetter 格式无效：'$DriveLetter'。请写成类似 'Z:'。"
    }

    return $value
}

function Ensure-RegistryKey {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
}

function Get-LocalAdministratorsGroupName {
    $administratorsSid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
    return $administratorsSid.Translate([System.Security.Principal.NTAccount]).Value.Split('\')[-1]
}

function Get-LocalUserSidValue {
    param([Parameter(Mandatory)][string]$UserName)

    try {
        $account = New-Object System.Security.Principal.NTAccount("$env:COMPUTERNAME\$UserName")
        return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        return $null
    }
}

function Save-ImageFile {
    param(
        [Parameter(Mandatory)][System.Drawing.Image]$Image,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('Png', 'Bmp', 'Jpeg')][string]$Format
    )

    $codec = switch ($Format) {
        'Png'  { [System.Drawing.Imaging.ImageFormat]::Png; break }
        'Bmp'  { [System.Drawing.Imaging.ImageFormat]::Bmp; break }
        'Jpeg' { [System.Drawing.Imaging.ImageFormat]::Jpeg; break }
    }

    $Image.Save($Path, $codec)
}

function New-ResizedBitmap {
    param(
        [Parameter(Mandatory)][System.Drawing.Image]$SourceImage,
        [Parameter(Mandatory)][int]$Size
    )

    $bitmap = New-Object System.Drawing.Bitmap($Size, $Size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)

    try {
        $graphics.Clear([System.Drawing.Color]::White)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

        $srcWidth  = [double]$SourceImage.Width
        $srcHeight = [double]$SourceImage.Height
        $scale     = [Math]::Min($Size / $srcWidth, $Size / $srcHeight)

        $targetWidth  = [int][Math]::Round($srcWidth * $scale)
        $targetHeight = [int][Math]::Round($srcHeight * $scale)
        $offsetX      = [int][Math]::Floor(($Size - $targetWidth) / 2)
        $offsetY      = [int][Math]::Floor(($Size - $targetHeight) / 2)

        $graphics.DrawImage($SourceImage, $offsetX, $offsetY, $targetWidth, $targetHeight)
        return $bitmap
    }
    finally {
        $graphics.Dispose()
    }
}

# ============================================================
# 核心功能 1：创建本地管理员账户
# ============================================================

function Ensure-LocalAdminUser {
    param(
        [Parameter(Mandatory)][string]$UserName,
        [Parameter()][string]$PasswordPlain
    )

    Write-Step "1/3 创建或校验本地管理员账户：$UserName"

    Import-Module Microsoft.PowerShell.LocalAccounts -ErrorAction SilentlyContinue

    $userExists = $false
    if (Get-Command Get-LocalUser -ErrorAction SilentlyContinue) {
        $userExists = [bool](Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue)
    }
    else {
        $netUserCheck = cmd /c "net user `"$UserName`""
        $userExists = ($LASTEXITCODE -eq 0)
        $null = $netUserCheck
    }

    if (-not $userExists) {
        if (Get-Command New-LocalUser -ErrorAction SilentlyContinue) {
            if ([string]::IsNullOrWhiteSpace($PasswordPlain)) {
                Write-Warn "LocalPasswordPlain 为空，将创建无密码管理员账户。建议后续立刻设置强密码。"
                New-LocalUser -Name $UserName `
                              -FullName $UserName `
                              -Description 'Created during Windows 11 OOBE by USB PowerShell script.' `
                              -NoPassword `
                              -AccountNeverExpires | Out-Null
            }
            else {
                $securePassword = ConvertTo-SecureString $PasswordPlain -AsPlainText -Force
                New-LocalUser -Name $UserName `
                              -FullName $UserName `
                              -Description 'Created during Windows 11 OOBE by USB PowerShell script.' `
                              -Password $securePassword `
                              -AccountNeverExpires `
                              -PasswordNeverExpires | Out-Null
            }
        }
        else {
            if ([string]::IsNullOrWhiteSpace($PasswordPlain)) {
                cmd /c "net user `"$UserName`" /add /active:yes /expires:never"
            }
            else {
                cmd /c "net user `"$UserName`" `"$PasswordPlain`" /add /active:yes /expires:never"
            }

            if ($LASTEXITCODE -ne 0) {
                throw "使用 net user 创建账户失败。"
            }
        }

        Write-Info "本地账户已创建：$UserName"
    }
    else {
        Write-Info "本地账户已存在：$UserName"
        if (Get-Command Enable-LocalUser -ErrorAction SilentlyContinue) {
            Enable-LocalUser -Name $UserName -ErrorAction SilentlyContinue
        }
    }

    $administratorsSid  = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $administratorsName = Get-LocalAdministratorsGroupName

    $alreadyInGroup = $false
    if (Get-Command Get-LocalGroupMember -ErrorAction SilentlyContinue) {
        try {
            $alreadyInGroup = [bool](Get-LocalGroupMember -SID $administratorsSid -ErrorAction Stop |
                Where-Object { $_.Name -match "\\$([regex]::Escape($UserName))$" })
        }
        catch {
            $alreadyInGroup = $false
        }
    }
    else {
        $members = cmd /c "net localgroup `"$administratorsName`""
        if ($LASTEXITCODE -eq 0 -and ($members -match "(?m)^\s*$([regex]::Escape($UserName))\s*$")) {
            $alreadyInGroup = $true
        }
    }

    if (-not $alreadyInGroup) {
        if (Get-Command Add-LocalGroupMember -ErrorAction SilentlyContinue) {
            Add-LocalGroupMember -SID $administratorsSid -Member $UserName -ErrorAction Stop
        }
        else {
            cmd /c "net localgroup `"$administratorsName`" `"$UserName`" /add"
            if ($LASTEXITCODE -ne 0) {
                throw "将 $UserName 加入本地 Administrators 组失败。"
            }
        }

        Write-Info "已将 $UserName 加入本地管理员组。"
    }
    else {
        Write-Info "$UserName 已在本地管理员组中。"
    }
}

# ============================================================
# 核心功能 2：设置头像
# ============================================================

function Set-AccountPicture {
    param(
        [Parameter(Mandatory)][string]$UserName,
        [Parameter()][string]$AvatarFileName
    )

    Write-Step "2/3 设置账户头像"

    if ([string]::IsNullOrWhiteSpace($AvatarFileName)) {
        Write-Warn "AvatarFileName 为空，已跳过头像设置。你后续只需把文件名填进去再重跑脚本。"
        return
    }

    $avatarPath = Join-Path -Path $PSScriptRoot -ChildPath $AvatarFileName
    if (-not (Test-Path -LiteralPath $avatarPath)) {
        Write-Warn "未找到头像文件：$avatarPath"
        Write-Warn "请确认头像文件与脚本位于同一文件夹，并正确填写 AvatarFileName。"
        return
    }

    Add-Type -AssemblyName System.Drawing

    $pictureFolder = Join-Path $env:ProgramData 'Microsoft\User Account Pictures'
    if (-not (Test-Path -LiteralPath $pictureFolder)) {
        New-Item -ItemType Directory -Path $pictureFolder -Force | Out-Null
    }

    $sourceImage = [System.Drawing.Image]::FromFile($avatarPath)

    try {
        $sizeMap = @{
            'user-32.png'  = 32
            'user-40.png'  = 40
            'user-48.png'  = 48
            'user-96.png'  = 96
            'user-192.png' = 192
            'user-200.png' = 200
            'user-240.png' = 240
            'user-448.png' = 448
        }

        foreach ($entry in $sizeMap.GetEnumerator()) {
            $targetPath = Join-Path $pictureFolder $entry.Key
            $bitmap = New-ResizedBitmap -SourceImage $sourceImage -Size $entry.Value
            try {
                Save-ImageFile -Image $bitmap -Path $targetPath -Format Png
            }
            finally {
                $bitmap.Dispose()
            }
        }

        $fullPng = Join-Path $pictureFolder 'user.png'
        $fullBmp = Join-Path $pictureFolder 'user.bmp'
        $fullJpg = Join-Path $pictureFolder 'user.jpg'

        $squareSize = [Math]::Max($sourceImage.Width, $sourceImage.Height)
        $mainBitmap = New-ResizedBitmap -SourceImage $sourceImage -Size $squareSize
        try {
            Save-ImageFile -Image $mainBitmap -Path $fullPng -Format Png
            Save-ImageFile -Image $mainBitmap -Path $fullBmp -Format Bmp
            Save-ImageFile -Image $mainBitmap -Path $fullJpg -Format Jpeg
        }
        finally {
            $mainBitmap.Dispose()
        }
    }
    finally {
        $sourceImage.Dispose()
    }

    $policyPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'
    Ensure-RegistryKey -Path $policyPath
    New-ItemProperty -Path $policyPath -Name 'UseDefaultTile' -PropertyType DWord -Value 1 -Force | Out-Null

    $userSid = Get-LocalUserSidValue -UserName $UserName
    if ($userSid) {
        try {
            $accountPictureRegPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AccountPicture\Users\$userSid"
            Ensure-RegistryKey -Path $accountPictureRegPath

            New-ItemProperty -Path $accountPictureRegPath -Name 'Image32'  -PropertyType String -Value (Join-Path $pictureFolder 'user-32.png')  -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image40'  -PropertyType String -Value (Join-Path $pictureFolder 'user-40.png')  -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image48'  -PropertyType String -Value (Join-Path $pictureFolder 'user-48.png')  -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image96'  -PropertyType String -Value (Join-Path $pictureFolder 'user-96.png')  -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image192' -PropertyType String -Value (Join-Path $pictureFolder 'user-192.png') -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image200' -PropertyType String -Value (Join-Path $pictureFolder 'user-200.png') -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image240' -PropertyType String -Value (Join-Path $pictureFolder 'user-240.png') -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'Image448' -PropertyType String -Value (Join-Path $pictureFolder 'user-448.png') -Force | Out-Null
            New-ItemProperty -Path $accountPictureRegPath -Name 'SourceId' -PropertyType DWord -Value 0 -Force | Out-Null

            Write-Info "已为账户 $UserName 写入头像相关注册表。"
        }
        catch {
            Write-Warn "已写入默认头像文件，但未能写入账户头像注册表：$($_.Exception.Message)"
        }
    }
    else {
        Write-Warn "未获取到用户 SID，仅写入了默认账户头像文件和 UseDefaultTile 策略。"
    }

    Write-Info "头像处理完成。"
}

# ============================================================
# 核心功能 3：挂载 NAS SMB 网络磁盘
# ============================================================

function Mount-NasShare {
    param(
        [Parameter()][string]$RemotePath,
        [Parameter(Mandatory)][string]$DriveLetter,
        [Parameter()][string]$UserName,
        [Parameter()][string]$PasswordPlain
    )

    Write-Step "3/3 挂载 NAS 网络磁盘"

    if ([string]::IsNullOrWhiteSpace($RemotePath)) {
        Write-Warn "NasRemotePath 为空，已跳过 NAS 挂载。示例：\\192.168.1.20\Public"
        return
    }

    if ($RemotePath -notmatch '^[\\]{2}[^\\]+\\[^\\]+') {
        throw "NasRemotePath 格式无效：'$RemotePath'。必须写成 \\服务器\共享名，例如 \\192.168.1.20\Public"
    }

    $normalizedDriveLetter = Normalize-DriveLetter -DriveLetter $DriveLetter
    $existingMapping = Get-SmbMapping -ErrorAction SilentlyContinue | Where-Object { $_.LocalPath -eq $normalizedDriveLetter }

    if ($existingMapping) {
        if ($existingMapping.RemotePath -ieq $RemotePath) {
            Write-Info "相同映射已存在：$normalizedDriveLetter -> $RemotePath"
            return
        }

        throw "盘符 $normalizedDriveLetter 已被占用，当前映射为 $($existingMapping.RemotePath)。请修改 NasDriveLetter 后重试。"
    }

    $hasUser = -not [string]::IsNullOrWhiteSpace($UserName)
    $hasPass = -not [string]::IsNullOrWhiteSpace($PasswordPlain)

    if ($hasUser -xor $hasPass) {
        throw "NasUserName 和 NasPasswordPlain 必须同时填写，或者同时留空。"
    }

    if (Get-Command New-SmbMapping -ErrorAction SilentlyContinue) {
        $params = @{
            LocalPath     = $normalizedDriveLetter
            RemotePath    = $RemotePath
            Persistent    = $true
            GlobalMapping = $true
        }

        if ($hasUser) {
            $params['UserName']        = $UserName
            $params['Password']        = $PasswordPlain
            $params['SaveCredentials'] = $true
        }

        try {
            New-SmbMapping @params | Out-Null
            Write-Info "已挂载 NAS：$normalizedDriveLetter -> $RemotePath"
            Write-Info "由于脚本通常在 OOBE 的 SYSTEM 上下文运行，这里使用了 GlobalMapping，方便后续登录用户直接看到映射。"
            return
        }
        catch {
            Write-Warn "New-SmbMapping 挂载失败：$($_.Exception.Message)"
        }
    }

    Write-Warn "将尝试使用 net use 兜底挂载。注意：如果脚本运行在 SYSTEM 上下文，兜底映射不一定会自动出现在后续登录用户会话中。"

    if ($hasUser) {
        cmd /c "net use $normalizedDriveLetter `"$RemotePath`" `"$PasswordPlain`" /user:`"$UserName`" /persistent:yes"
    }
    else {
        cmd /c "net use $normalizedDriveLetter `"$RemotePath`" /persistent:yes"
    }

    if ($LASTEXITCODE -ne 0) {
        throw "兜底方式 net use 挂载也失败了。请检查 NAS 路径、共享名、账号密码和 SMB 配置。"
    }

    Write-Info "已通过 net use 挂载 NAS：$normalizedDriveLetter -> $RemotePath"
}

# ============================================================
# 核心功能 4：暂停 Windows 更新活动
# ============================================================

function Pause-WindowsUpdateActivity {
    Write-Step "附加步骤：暂停 Windows 更新活动"

    $today       = Get-Date
    $startString = $today.ToString('yyyy-MM-dd')
    $endString   = $today.AddDays(35).ToString('yyyy-MM-dd')

    $wuPolicyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
    Ensure-RegistryKey -Path $wuPolicyPath

    New-ItemProperty -Path $wuPolicyPath -Name 'PauseFeatureUpdates'          -PropertyType DWord  -Value 1            -Force | Out-Null
    New-ItemProperty -Path $wuPolicyPath -Name 'PauseQualityUpdates'          -PropertyType DWord  -Value 1            -Force | Out-Null
    New-ItemProperty -Path $wuPolicyPath -Name 'PauseFeatureUpdatesStartTime' -PropertyType String -Value $startString -Force | Out-Null
    New-ItemProperty -Path $wuPolicyPath -Name 'PauseQualityUpdatesStartTime' -PropertyType String -Value $startString -Force | Out-Null

    Write-Info "已写入 Windows Update 暂停策略。"
    Write-Info "暂停开始日期：$startString"
    Write-Info "按常见策略窗口估算，预计约在 $endString 左右恢复。"

    foreach ($serviceName in @('wuauserv', 'UsoSvc', 'bits', 'dosvc')) {
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if (-not $service) {
            continue
        }

        if ($service.Status -ne 'Stopped') {
            try {
                Stop-Service -Name $serviceName -Force -ErrorAction Stop
                Write-Info "已停止服务：$serviceName"
            }
            catch {
                Write-Warn "未能停止服务 $serviceName：$($_.Exception.Message)"
            }
        }
    }
}

# ============================================================
# 主流程
# ============================================================

Write-Host ''
Write-Host 'LapTalk Windows 11 OOBE 自动配置脚本' -ForegroundColor Yellow
Write-Host "脚本目录：$PSScriptRoot" -ForegroundColor DarkYellow
Write-Host ''

Ensure-LocalAdminUser -UserName $Config.LocalUserName -PasswordPlain $Config.LocalPasswordPlain
Set-AccountPicture    -UserName $Config.LocalUserName -AvatarFileName $Config.AvatarFileName
Mount-NasShare        -RemotePath $Config.NasRemotePath -DriveLetter $Config.NasDriveLetter -UserName $Config.NasUserName -PasswordPlain $Config.NasPasswordPlain
Pause-WindowsUpdateActivity

Write-Step "执行完成"
Write-Info "建议现在手动重启一次，让头像、网络映射和更新策略更稳定地生效。"
Write-Info "重启命令：shutdown /r /t 0"
