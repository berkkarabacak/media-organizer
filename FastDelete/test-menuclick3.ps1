Add-Type -AssemblyName UIAutomationClient, System.Drawing
$src = @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class EK {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
Add-Type -TypeDefinition $src -ReferencedAssemblies System.Drawing

$root=[System.Windows.Automation.AutomationElement]::RootElement
$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'FastDelete')
$w=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)
$bs=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
foreach($b in $bs){ if($b.Current.Name -eq 'DBG MENU'){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Write-Output 'DBG CLICKED' } }
Start-Sleep -Milliseconds 600

# find the popup hwnd (small visible window of this process)
$procId = [uint32](Get-Process FastDelete).Id
$script:popup = [IntPtr]::Zero
$cb = [EK+EnumProc]{
  param([IntPtr]$h, [IntPtr]$l)
  $p = [uint32]0
  [void][EK]::GetWindowThreadProcessId($h, [ref]$p)
  if ($p -eq $script:procId -and [EK]::IsWindowVisible($h)) {
    $rc = New-Object EK+RECT
    [void][EK]::GetWindowRect($h, [ref]$rc)
    $ww = $rc.Right - $rc.Left; $hh = $rc.Bottom - $rc.Top
    if ($ww -lt 600 -and $ww -gt 40 -and $hh -gt 20) { $script:popup = $h }
  }
  return $true
}
$script:procId = $procId
[void][EK]::EnumWindows($cb, [IntPtr]::Zero)
if ($script:popup -eq [IntPtr]::Zero) { Write-Output 'NO POPUP'; exit 1 }
Write-Output ("popup hwnd: $($script:popup)")

# UIA element from hwnd, enumerate menu items
$el = [System.Windows.Automation.AutomationElement]::FromHandle($script:popup)
$mi = $null
$items = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem)))
foreach($it in $items){ Write-Output ('  [' + $it.Current.Name + ']'); if($it.Current.Name -like 'Delete*'){ $mi=$it } }
if(-not $mi){ Write-Output 'NOT FOUND'; exit 1 }
$mi.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'DELETE INVOKED FROM MENU'
Start-Sleep -Seconds 1
$btn=$null
$bs2=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
foreach($b in $bs2){ if($b.Current.Name -eq 'Yes, delete forever'){ $btn=$b } }
if(-not $btn){ Write-Output 'no confirm dialog'; exit 1 }
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
