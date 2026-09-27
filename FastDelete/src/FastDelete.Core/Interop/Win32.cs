using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace FastDelete.Core.Interop;

/// <summary>Win32/NT interop. NOTE: never declare a GetLastError P/Invoke stub -
/// a stub clobbers the last-error value; always use Marshal.GetLastWin32Error().</summary>
public static class Win32
{
    public const uint FILE_ATTRIBUTE_DIRECTORY = 0x10;
    public const uint FILE_ATTRIBUTE_READONLY = 0x1;
    public const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x400;

    public const uint GENERIC_READ = 0x80000000;
    public const uint GENERIC_WRITE = 0x40000000;
    public const uint DELETE = 0x00010000;

    public const uint FILE_SHARE_READ = 1;
    public const uint FILE_SHARE_WRITE = 2;
    public const uint FILE_SHARE_DELETE = 4;

    public const uint CREATE_NEW = 1;
    public const uint OPEN_EXISTING = 3;

    public const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    public const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    public const uint FILE_FLAG_POSIX_SEMANTICS = 0x01000000;
    public const uint FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000;

    public const int ERROR_FILE_NOT_FOUND = 2;
    public const int ERROR_PATH_NOT_FOUND = 3;
    public const int ERROR_ACCESS_DENIED = 5;
    public const int ERROR_INVALID_HANDLE = 6;
    public const int ERROR_INVALID_DATA = 13;
    public const int ERROR_NOT_SUPPORTED = 50;
    public const int ERROR_INVALID_PARAMETER = 87;
    public const int ERROR_DIR_NOT_EMPTY = 145;
    public const int ERROR_SHARING_VIOLATION = 32;
    public const int ERROR_LOCK_VIOLATION = 33;
    public const int ERROR_PRIVILEGE_NOT_HELD = 1314;

    public const uint FILE_DEVICE_FILE_SYSTEM = 0x00000009;
    public const uint FILE_ANY_ACCESS = 0;
    public const uint METHOD_BUFFERED = 0;
    public const uint FSCTL_GET_REPARSE_POINT = 0x000900A8;    // CTL_CODE(FILE_DEVICE_FILE_SYSTEM, 42, METHOD_BUFFERED, FILE_ANY_ACCESS)
    public const uint FSCTL_DELETE_REPARSE_POINT = 0x000900AC; // CTL_CODE(FILE_DEVICE_FILE_SYSTEM, 43, METHOD_BUFFERED, FILE_ANY_ACCESS)
    public const uint FSCTL_SET_REPARSE_POINT = 0x000900A4;    // CTL_CODE(FILE_DEVICE_FILE_SYSTEM, 41, METHOD_BUFFERED, FILE_ANY_ACCESS)
    public const int REPARSE_DATA_BUFFER_HEADER_SIZE = 8;
    public const uint IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003;
    public const uint IO_REPARSE_TAG_SYMLINK = 0xA000000C;

    // NtSetInformationFile / FileDispositionInformationEx.
    // NOTE: the Win32 SetFileInformationByHandle wrapper rejects FileDispositionInfoEx with
    // ERROR_INVALID_PARAMETER on some systems; the ntdll path works everywhere the FS supports it.
    public const int FileDispositionInformationEx = 64;   // NT FILE_INFORMATION_CLASS
    public const uint FILE_DISPOSITION_DO_NOT_DELETE = 0x0;
    public const uint FILE_DISPOSITION_DELETE = 0x1;
    public const uint FILE_DISPOSITION_POSIX_SEMANTICS = 0x2;
    public const uint FILE_DISPOSITION_FORCE_IMAGE_SECTION_CHECK = 0x4;
    public const uint FILE_DISPOSITION_ON_CLOSE = 0x8;
    public const uint FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE = 0x10;

    // SHFileOperation
    public const uint FO_DELETE = 0x3;
    public const uint FOF_MULTIDESTFILES = 0x1;
    public const uint FOF_SILENT = 0x4;
    public const uint FOF_NOCONFIRMATION = 0x10;
    public const uint FOF_ALLOWUNDO = 0x40;
    public const uint FOF_NOERRORUI = 0x400;
    public const uint FOF_WANTNUKEWARNING = 0x4000;
    public const uint FOF_NO_UI = FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct WIN32_FIND_DATA
    {
        public uint dwFileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftCreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftLastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftLastWriteTime;
        public uint nFileSizeHigh;
        public uint nFileSizeLow;
        public uint dwReserved0;
        public uint dwReserved1;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string cFileName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)]
        public string cAlternateFileName;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct FILE_DISPOSITION_INFO_EX
    {
        public uint Flags;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct SHFILEOPSTRUCT
    {
        public IntPtr hwnd;
        public uint wFunc;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string pFrom;
        public IntPtr pTo;
        public ushort fFlags;
        public int fAnyOperationsAborted;
        public IntPtr hNameMappings;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string? lpszProgressTitle;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr FindFirstFileExW(
        string lpFileName,
        FINDEX_INFO_LEVELS fInfoLevelId,
        out WIN32_FIND_DATA lpFindFileData,
        FINDEX_SEARCH_OPS fSearchOp,
        IntPtr lpSearchFilter,
        int dwAdditionalFlags);

    public enum FINDEX_INFO_LEVELS : int
    {
        FindExInfoStandard = 0,
        FindExInfoBasic = 1,   // skips 8.3 short-name lookup - big win on huge NTFS dirs
        FindExInfoMaxInfoLevel = 2
    }

    public enum FINDEX_SEARCH_OPS : int
    {
        FindExSearchNameMatch = 0,
        FindExSearchLimitToDirectories = 1,
        FindExSearchLimitToDevices = 2,
        FindExSearchMaxSearchOp = 3
    }

    public const int FIND_FIRST_EX_LARGE_FETCH = 0x2;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FindNextFileW(IntPtr hFindFile, out WIN32_FIND_DATA lpFindFileData);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FindClose(IntPtr hFindFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteFileW(string lpFileName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool RemoveDirectoryW(string lpPathName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetFileAttributesW(string lpFileName, uint dwFileAttributes);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetFileAttributesW(string lpFileName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern SafeFileHandle CreateFileW(
        string lpFileName,
        uint dwDesiredAccess,
        uint dwShareMode,
        IntPtr lpSecurityAttributes,
        uint dwCreationDisposition,
        uint dwFlagsAndAttributes,
        IntPtr hTemplateFile);

    [DllImport("ntdll.dll")]
    public static extern int NtSetInformationFile(
        SafeFileHandle hFile,
        out IO_STATUS_BLOCK IoStatusBlock,
        ref FILE_DISPOSITION_INFO_EX FileInformation,
        int Length,
        int FileInformationClass);

    [DllImport("ntdll.dll")]
    public static extern uint RtlNtStatusToDosError(int Status);

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_STATUS_BLOCK
    {
        public IntPtr Status;
        public IntPtr Information;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle hFile,
        StringBuilder lpszFilePath,
        uint cchFilePath,
        uint dwFlags);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeviceIoControl(
        SafeFileHandle hDevice,
        uint dwIoControlCode,
        IntPtr lpInBuffer,
        int nInBufferSize,
        IntPtr lpOutBuffer,
        int nOutBufferSize,
        out int lpBytesReturned,
        IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern ulong GetTickCount64();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint FormatMessageW(
        uint dwFlags,
        IntPtr lpSource,
        int dwMessageId,
        uint dwLanguageId,
        [Out] StringBuilder lpBuffer,
        int nSize,
        IntPtr arguments);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHFileOperationW(ref SHFILEOPSTRUCT FileOp);

    public static bool IsDirectory(uint attributes) => (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    public static bool IsReparsePoint(uint attributes) => (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
    public static bool IsReadOnly(uint attributes) => (attributes & FILE_ATTRIBUTE_READONLY) != 0;

    /// <summary>System error text for the failure report. FormatMessageW verified working (flags 0x1200).</summary>
    public static string GetErrorMessage(int errorCode)
    {
        var sb = new StringBuilder(512);
        uint result = FormatMessageW(
            0x1200, // FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS
            IntPtr.Zero,
            errorCode,
            0,
            sb,
            sb.Capacity,
            IntPtr.Zero);
        if (result == 0)
            return $"error 0x{errorCode:X8}";
        var msg = sb.ToString();
        return msg.TrimEnd('\r', '\n');
    }
}
