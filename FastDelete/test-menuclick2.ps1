Add-Type -AssemblyName UIAutomationClient
$root=[System.Windows.Automation.AutomationElement]::RootElement
$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'FastDelete')
$w=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)
$bs=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
foreach($b in $bs){ if($b.Current.Name -eq 'DBG MENU'){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Write-Output 'DBG CLICKED' } }
Start-Sleep -Milliseconds 600
$mi=$null
$menus=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Menu)))
Write-Output ('menus: ' + $menus.Count)
foreach($m in $menus){
  $items=$m.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem)))
  foreach($it in $items){ Write-Output ('  [' + $it.Current.Name + ']'); if($it.Current.Name -like 'Delete*'){ $mi=$it } }
}
if(-not $mi){ Write-Output 'NOT FOUND'; exit 1 }
$mi.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'DELETE INVOKED'
Start-Sleep -Seconds 1
$btn=$null
$bs2=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
foreach($b in $bs2){ if($b.Current.Name -eq 'Yes, delete forever'){ $btn=$b } }
if(-not $btn){ Write-Output 'no confirm'; exit 1 }
$ts=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Text)))
foreach($t in $ts){ if($t.Current.Name -match 'FOREVER'){ Write-Output ('DIALOG: ' + $t.Current.Name) } }
$btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'CONFIRMED'
$tree='C:\Users\OdinLocal\AppData\Local\Temp\fde2e\tiny10k'
for($i=0;$i -lt 90;$i++){
  $dirs = @(Get-ChildItem -LiteralPath $tree -Directory -ErrorAction SilentlyContinue)
  if($dirs.Count -le 97){ Write-Output ('dirs left: ' + $dirs.Count + ' after ' + $i + 's'); break }
  Start-Sleep -Seconds 1
}
