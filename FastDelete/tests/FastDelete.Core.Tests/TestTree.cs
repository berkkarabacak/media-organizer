using System.Diagnostics;
using System.Runtime.InteropServices;
using FastDelete.Core.Deletion;

namespace FastDelete.Core.Tests;

/// <summary>Test helpers: temp-tree creation, junction/symlink creation, Win32 interop for tests.</summary>
internal static class TestTree
{
    public static string CreateTempRoot([System.Runtime.CompilerServices.CallerMemberName] string name = "")
    {
        string root = Path.Combine(Path.GetTempPath(), "FastDeleteTests", name + "_" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(root);
        return root;
    }

    public static string MakeFiles(string root, int filesPerDir, int dirs, string prefix = "f")
    {
        Directory.CreateDirectory(root);
        for (int d = 0; d < dirs; d++)
        {
            string dir = Path.Combine(root, "dir" + d);
            Directory.CreateDirectory(dir);
            for (int f = 0; f < filesPerDir; f++)
                File.WriteAllText(Path.Combine(dir, $"{prefix}{d}_{f}.txt"), "x");
        }
        return root;
    }

    public static int CountEntries(string root)
    {
        int count = 0;
        foreach (var _ in Directory.EnumerateFileSystemEntries(root, "*", SearchOption.AllDirectories))
            count++;
        return count;
    }

    public static void CreateJunction(string junctionPath, string targetPath)
    {
        var psi = new ProcessStartInfo("cmd.exe", $"/c mklink /J \"{junctionPath}\" \"{targetPath}\"")
        {
            CreateNoWindow = true,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        var p = Process.Start(psi)!;
        p.WaitForExit();
        if (p.ExitCode != 0)
            throw new InvalidOperationException($"mklink /J failed: {p.StandardError.ReadToEnd()}");
    }

    /// <summary>
    /// Creates a real symlink via FSCTL_SET_REPARSE_POINT - works WITHOUT the
    /// SeCreateSymbolicLink privilege that CreateSymbolicLink requires.
    /// </summary>
    public static void CreateSymlink(string linkPath, string targetPath, bool directory)
    {
        if (directory)
            Directory.CreateDirectory(linkPath);
        else
            File.WriteAllText(linkPath, string.Empty);

        const uint GENERIC_WRITE = 0x40000000;
        const uint FILE_SHARE_READ = 1;
        const uint FILE_SHARE_WRITE = 2;
        const uint FILE_SHARE_DELETE = 4;
        const uint OPEN_EXISTING = 3;
        const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        const uint FSCTL_SET_REPARSE_POINT = 0x000900A4;

        using var handle = CreateFileW(@"\\?\" + linkPath, GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
        if (handle.IsInvalid)
            throw new InvalidOperationException($"Cannot open {linkPath} for reparse setup: {Marshal.GetLastWin32Error()}");

        string substitute = @"\??\" + targetPath;
        string print = targetPath;
        byte[] subBytes = System.Text.Encoding.Unicode.GetBytes(substitute);
        byte[] printBytes = System.Text.Encoding.Unicode.GetBytes(print);

        // REPARSE_DATA_BUFFER:
        //   header: tag(4) datalen(2) reserved(2)
        //   data:   subOff(2) subLen(2) printOff(2) printLen(2) flags(4) PathBuffer[]
        // Offsets are relative to PathBuffer (start of the data area).
        int dataLen = 12 + subBytes.Length + printBytes.Length;
        int total = 8 + dataLen;
        var buffer = new byte[total];
        int offset = 0;
        void WriteU32(uint v) { BitConverter.GetBytes(v).CopyTo(buffer, offset); offset += 4; }
        void WriteU16(ushort v) { BitConverter.GetBytes(v).CopyTo(buffer, offset); offset += 2; }

        WriteU32(0xA000000C); // IO_REPARSE_TAG_SYMLINK
        WriteU16((ushort)dataLen); // ReparseDataLength
        WriteU16(0);               // Reserved
        WriteU16(0);                       // SubstituteNameOffset (relative to PathBuffer)
        WriteU16((ushort)subBytes.Length); // SubstituteNameLength
        WriteU16((ushort)subBytes.Length); // PrintNameOffset
        WriteU16((ushort)printBytes.Length); // PrintNameLength
        WriteU32(0);                    // Flags (absolute target)
        subBytes.CopyTo(buffer, offset); offset += subBytes.Length;
        printBytes.CopyTo(buffer, offset);

        IntPtr native = Marshal.AllocHGlobal(total);
        try
        {
            Marshal.Copy(buffer, 0, native, total);
            bool ok = DeviceIoControl(handle, FSCTL_SET_REPARSE_POINT,
                native, total, IntPtr.Zero, 0, out _, IntPtr.Zero);
            if (!ok)
                throw new InvalidOperationException($"FSCTL_SET_REPARSE_POINT failed on {linkPath}: {Marshal.GetLastWin32Error()}");
        }
        finally
        {
            Marshal.FreeHGlobal(native);
        }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFileW(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(
        Microsoft.Win32.SafeHandles.SafeFileHandle hDevice,
        uint dwIoControlCode,
        IntPtr lpInBuffer, int nInBufferSize,
        IntPtr lpOutBuffer, int nOutBufferSize,
        out int lpBytesReturned, IntPtr lpOverlapped);
}
