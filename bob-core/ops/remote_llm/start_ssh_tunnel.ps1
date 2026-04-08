param(
    [int]$LocalPort = 18001,
    [string]$RemoteHost = "ssh.rionnag.net",
    [string]$IdentityPath = "$HOME\\.ssh\\id_ed25519_bob_llm"
)

$forwardSpec = "127.0.0.1:$LocalPort:127.0.0.1:8001"
$existing = Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" |
    Where-Object { $_.CommandLine -like "*$forwardSpec*" }

if ($existing) {
    Write-Output "ssh_tunnel_pid=$($existing[0].ProcessId)"
    Write-Output "base_url=http://127.0.0.1:$LocalPort/v1"
    exit 0
}

$process = Start-Process `
    -FilePath ssh.exe `
    -ArgumentList "-i", $IdentityPath, "-N", "-L", $forwardSpec, $RemoteHost `
    -PassThru

Start-Sleep -Seconds 2

Write-Output "ssh_tunnel_pid=$($process.Id)"
Write-Output "base_url=http://127.0.0.1:$LocalPort/v1"
