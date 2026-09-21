$ErrorActionPreference = 'Continue'
$b = 'http://127.0.0.1:5000'
$j = Join-Path $env:TEMP 'pg-notify'
Remove-Item $j -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $j | Out-Null

# 1) Admin login
$tok = [regex]::Match((curl.exe -s -c "$j\a.jar" "$b/login" | Out-String), 'name="csrf_token"[^>]*value="([^"]+)"').Groups[1].Value
$r = curl.exe -s -b "$j\a.jar" -c "$j\a.jar" -o NUL -w "%{http_code}" -d "csrf_token=$tok" -d "email=adminpolicyguard@gmail.com" -d "password=admin123" "$b/login"
Write-Host "1) admin login: $r"

# 2) Harvest token from expiring page and trigger the engine globally
$page = curl.exe -s -b "$j\a.jar" "$b/policies/expiring" | Out-String
$tok2 = [regex]::Match($page, 'name="csrf_token"[^>]*value="([^"]+)"').Groups[1].Value
$out = curl.exe -s -b "$j\a.jar" -o "$j\after.html" -w "%{http_code}" -d "csrf_token=$tok2" "$b/policies/expiring/trigger-auto"
Write-Host "2) trigger-auto: $out"
$m = Select-String -Path "$j\after.html" -Pattern 'Reminder run:[^<]*' | ForEach-Object { $_.Matches.Value } | Select-Object -First 1
if (-not $m) { $m = Select-String -Path "$j\after.html" -Pattern 'No policies are due[^<]*' | ForEach-Object { $_.Matches.Value } | Select-Object -First 1 }
Write-Host "   flash: $m"

# 3) Notifications page shows both legs
$html = curl.exe -s -b "$j\a.jar" "$b/notifications/" | Out-String
Write-Host "3a) 'Policy expiring' present:" ($html.Contains('Policy expiring'))
Write-Host "3b) SMS success notice present:" ($html.Contains('Renewal reminder sent'))
Write-Host "3c) customer name in body:" ($html.Contains('Daniel Kiprop'))

# 4) Dashboard carries the unread badge
$dash = curl.exe -s -b "$j\a.jar" "$b/" | Out-String
Write-Host "4) bell badge on dashboard:" ($dash -match 'aria-label="Notifications"[\s\S]{0,400}?rounded-full')

# 5) Mark all read -> API reports zero
curl.exe -s -b "$j\a.jar" -o NUL -w "5) mark-all: %{http_code}`n" -X POST "$b/notifications/mark-all"
$tok3 = [regex]::Match((curl.exe -s -b "$j\a.jar" "$b/notifications/" | Out-String), 'name="csrf_token"[^>]*value="([^"]+)"').Groups[1].Value
# mark-all posts need CSRF too; redo with token if first attempt was blocked
$api = curl.exe -s -b "$j\a.jar" "$b/notifications/api/unread"
Write-Host "   unread after mark-all: $api"
