Add-Type -AssemblyName UIAutomationClient, System.Drawing
$src = @"
using System;
using System.Runtime.InteropServices;
public class PD {
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
}
"@
Add-Type -TypeDefinition $src

# the picker hwnd was 397292 earlier; verify it still exists
$hwnd = New-Object IntPtr(397292)
if (-not [PD]::IsWindow($hwnd)) {
  # re-find: enumerate for a visible #32770 of this process
  $src2 = @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class PE {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
  Add-Type -TypeDefinition $src2
  $procId = [uint32](Get-Process FastDelete).Id
  $script:found = [IntPtr]::Zero
  $cb = [PE+EnumProc]{
    param([IntPtr]$h, [IntPtr]$l)
    $p = [uint32]0
    [void][PE]::GetWindowThreadProcessId($h, [ref]$p)
    if ($p -eq $script:procId -and [PE]::IsWindowVisible($h)) {
      $cls = New-Object System.Text.StringBuilder 256
      [void][PE]::GetClassName($h, $cls, 256)
      if ($cls.ToString() -eq '#32770') { $script:found = $h }
    }
    return $true
  }
  $script:procId = $procId
  [void][PE]::EnumWindows($cb, [IntPtr]::Zero)
  $hwnd = $script:found
}
if ($hwnd -eq [IntPtr]::Zero) { Write-Output 'NO PICKER'; exit 1 }
Write-Output ("picker hwnd: $hwnd")

$el = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
# type the path into the Folder edit
$edit = $el.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)))
if (-not $edit) { Write-Output 'no edit'; exit 1 }
$edit.SetFocus() | Out-Null
$vp = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$vp.SetValue('C:\Users\OdinLocal\AppData\Local\Temp\fde2e\deep1k')
Start-Sleep -Milliseconds 400
# click Select Folder
$btn = $el.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.AndCondition(
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)),
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'Select Folder')))))
if (-not $btn) { Write-Output 'no select button'; exit 1 }
$btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Seconds 3
Write-Output 'SELECTED'
$root=[System.Windows.Automation.AutomationElement]::RootElement
$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'FastDelete')
$w=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)
$btns=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
foreach($b in $btns){ if($b.Current.Name -match 'deep1k|level0000'){ Write-Output ('BREADCRUMB: [' + $b.Current.Name + ']') } }
$sb=$w.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::StatusBar)))
$ts=$sb.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Text)))
foreach($t in $ts){ if($t.Current.Name -notmatch 'Switch'){ Write-Output ('STATUS: [' + $t.Current.Name + ']') } }
