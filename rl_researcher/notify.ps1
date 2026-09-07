<#
    One desktop toast, or a non-zero exit that says why.

    Called by rl_researcher/notify.py, which has already written the log line. This script is
    the courtesy channel: it is allowed to fail, and it must fail loudly enough on stderr that
    the caller can write down what went wrong instead of guessing.

    WinRT is reached through the runtime type activator rather than a module, so this needs no
    install and no admin: BurntToast is a lovely module and is not on most machines.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $false)][string]$Body = "",
    # The shell whose Start-menu identity the toast borrows. Windows refuses a toast from an
    # AppUserModelID it does not know, and PowerShell's own is one every machine with
    # PowerShell has by construction.
    [string]$AppId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = "Stop"

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
} catch {
    Write-Error "WinRT toast unavailable: $($_.Exception.Message)"
    exit 2
}

try {
    $texts = $template.GetElementsByTagName("text")
    $texts.Item(0).AppendChild($template.CreateTextNode($Title)) | Out-Null
    if ($Body) { $texts.Item(1).AppendChild($template.CreateTextNode($Body)) | Out-Null }

    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    # Long: this is a run that ended hours ago being reported to someone who was not there.
    # The default eight seconds is for a message you were already looking at the screen for.
    $toast.Tag = "rl-researcher"
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
} catch {
    Write-Error "toast refused: $($_.Exception.Message)"
    exit 3
}

exit 0
