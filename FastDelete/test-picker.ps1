Add-Type -AssemblyName System.Drawing
$src = @"
using System;
using System.Runtime.InteropServices;
public class PK {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT r);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
Add-Type -TypeDefinition $src -ReferencedAssemblies System.Drawing
$hwnd = New-Object IntPtr(397292)
$rc = New-Object PK+RECT
[void][PK]::GetWindowRect($hwnd, [ref]$rc)
$w = $rc.Right - $rc.Left; $h = $rc.Bottom - $rc.Top
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[void][PK]::PrintWindow($hwnd, $hdc, 3)
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save('C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer\FastDelete\picker-dialog.png')
Write-Output 'SAVED'
