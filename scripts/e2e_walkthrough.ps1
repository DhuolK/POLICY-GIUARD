# §20 live end-to-end walkthrough against the seeded dev DB.
$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:5000'
$jar  = Join-Path $env:TEMP 'pg-jars'
Remove-Item $jar -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $jar | Out-Null

function Get-Csrf($jarName, $url) {
    $tmp = "$jar\$jarName-csrf.html"
    curl.exe -s -b "$jar\$jarName.jar" -c "$jar\$jarName.jar" -o $tmp "$base$url"
    $html = Get-Content $tmp -Raw
    return [regex]::Match($html, 'name="csrf_token"[^>]*value="([^"]+)"').Groups[1].Value
}

function Login($user, $pass, $name) {
    # Harvest a fresh CSRF token from the login form, then post credentials.
    $tok = Get-Csrf $name '/login'
    if (-not $tok) { Write-Host "$name login FAILED to obtain csrf"; return }
    curl.exe -s -b "$jar\$name.jar" -c "$jar\$name.jar" -o "$jar\$name-login-result.html" `
        -w "%{http_code} -> %{redirect_url}`n" `
        -d "csrf_token=$tok" -d "email=$user" -d "password=$pass" "$base/login"
}

Write-Host '=== Logins ==='
Login 'adminpolicyguard@gmail.com' 'admin123'  'admin'
Login 'worker@policyguard.co.ke'   'worker123' 'wa'
Login 'worker2@policyguard.co.ke'  'worker123' 'wb'

Write-Host "`n=== Client list counts ==="
foreach ($n in 'admin','wa','wb') {
    curl.exe -s -b "$jar\$n.jar" -o "$jar\$n-clients.html" "$base/clients/"
    $html = Get-Content "$jar\$n-clients.html" -Raw
    $count = ([regex]::Matches($html, 'View Details')).Count
    Write-Host ("{0,-6} sees {1} clients" -f $n, $count)
}

Write-Host "`n=== IDOR probes ==="
curl.exe -s -b "$jar\wa.jar" -o NUL -w "WorkerA GET WorkerB client : %{http_code}`n" "$base/clients/6a8e007273d2bc6f00f053b4"
$tokWa = Get-Csrf wa '/clients/'
curl.exe -s -b "$jar\wa.jar" -o NUL -w "WorkerA POST assign       : %{http_code}`n" `
    -d "csrf_token=$tokWa" -d "worker_id=6a8e007173d2bc6f00f053ae" `
    "$base/clients/6a8e007273d2bc6f00f053b5/assign"

Write-Host "`n=== Admin reassignment of CL-010 (unassigned pool) ==="
$tokAdm = Get-Csrf admin '/clients/'
curl.exe -s -b "$jar\admin.jar" -c "$jar\admin.jar" -o "$jar\reassign.html" -L -w "%{http_code}`n" `
    -d "csrf_token=$tokAdm" --data-urlencode "worker_id=6a8e007173d2bc6f00f053ae" `
    "$base/clients/6a8e007273d2bc6f00f053b8/assign"
Select-String -Path "$jar\reassign.html" -Pattern 'reassigned successfully|is now unassigned' | ForEach-Object { $_.Matches.Value } | Select-Object -First 1

Write-Host "`n=== Staff enable/disable ==="
$tokUsr = Get-Csrf admin '/admin/users'
curl.exe -s -b "$jar\admin.jar" -o "$jar\disable.html" -L -w "disable worker2: %{http_code} " `
    -d "csrf_token=$tokUsr" -d "action=disable" "$base/admin/users/6a8e007273d2bc6f00f053ae/set-active"
Select-String -Path "$jar\disable.html" -Pattern 'has been disabled' | ForEach-Object { $_.Matches.Value } | Select-Object -First 1

# Disabled worker cannot start a new session:
$tokWb = Get-Csrf wb '/login'
curl.exe -s -o "$jar\wb-relogin.html" -w "worker2 login while disabled: %{http_code} -> %{redirect_url}`n" `
    -d "csrf_token=$tokWb" -d "email=worker2@policyguard.co.ke" -d "password=worker123" "$base/login"
$deactivated = Select-String -Path "$jar\wb-relogin.html" -Pattern 'deactivated' -Quiet
Write-Host "deactivation message shown: $deactivated"

curl.exe -s -b "$jar\admin.jar" -o "$jar\enable.html" -L -w "re-enable worker2: %{http_code} " `
    -d "csrf_token=$tokUsr" -d "action=enable" "$base/admin/users/6a8e007273d2bc6f00f053ae/set-active"

Write-Host "`n=== Self-disable guard ==="
curl.exe -s -b "$jar\admin.jar" -o "$jar\selfdis.html" -L -w "admin self-disable: %{http_code} " `
    -d "csrf_token=$tokUsr" -d "action=disable" "$base/admin/users/6a8e007173d2bc6f00f053ac/set-active"
Select-String -Path "$jar\selfdis.html" -Pattern 'cannot disable your own' | ForEach-Object { $_.Matches.Value } | Select-Object -First 1

Write-Host "`n=== Anonymous gate ==="
curl.exe -s -o NUL -w "anon /clients/: %{http_code} -> %{redirect_url}`n" "$base/clients/"
