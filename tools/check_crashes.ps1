$today = (Get-Date).Date
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'} -MaxEvents 50 -ErrorAction SilentlyContinue |
  Where-Object { $_.TimeCreated -gt $today } |
  Select-Object TimeCreated, Message |
  ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss') + ' :: ' + $_.Message.Substring(0, [Math]::Min(350, $_.Message.Length)) + "`n---" }
Write-Output '=== Windows Error Reporting (WER) ==='
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Windows Error Reporting'} -MaxEvents 50 -ErrorAction SilentlyContinue |
  Where-Object { $_.TimeCreated -gt $today } |
  Select-Object TimeCreated, Message |
  ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss') + ' :: ' + $_.Message.Substring(0, [Math]::Min(350, $_.Message.Length)) + "`n---" }
