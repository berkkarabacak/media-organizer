using System.Runtime.InteropServices;
using FastDelete.Core.Interop;

namespace FastDelete.Core.Deletion;

public enum DeleteStatus : byte
{
    Deleted,
    NotFound,          // vanished mid-run (another process) - treated as success
    DirectoryNotEmpty, // caller should retry after siblings finish
    Failed,
}

/// <summary>Single-item deletion primitives. Never throws; reports Win32 errors.</summary>
public static class FileDeleter
{
    public static uint GetAttributes(string prefixedPath)
        => Win32.GetFileAttributesW(prefixedPath);

    public static (DeleteStatus status, int error) DeleteFileClassic(string prefixedPath)
    {
        if (Win32.DeleteFileW(prefixedPath))
            return (DeleteStatus.Deleted, 0);

        int err = Marshal.GetLastWin32Error();
        return err switch
        {
            Win32.ERROR_FILE_NOT_FOUND or Win32.ERROR_PATH_NOT_FOUND => (DeleteStatus.NotFound, err),
            Win32.ERROR_ACCESS_DENIED => TryClearReadOnlyAndRetry(prefixedPath),
            _ => (DeleteStatus.Failed, err),
        };
    }

    private static (DeleteStatus status, int error) TryClearReadOnlyAndRetry(string prefixedPath)
    {
        // Clear the read-only attribute on the file and retry once.
        uint attrs = GetAttributes(prefixedPath);
        if (attrs != uint.MaxValue && Win32.IsReadOnly(attrs))
        {
            Win32.SetFileAttributesW(prefixedPath, attrs & ~Win32.FILE_ATTRIBUTE_READONLY);
        }
        if (Win32.DeleteFileW(prefixedPath))
            return (DeleteStatus.Deleted, 0);

        int err = Marshal.GetLastWin32Error();
        return err is Win32.ERROR_FILE_NOT_FOUND or Win32.ERROR_PATH_NOT_FOUND
            ? (DeleteStatus.NotFound, err)
            : (DeleteStatus.Failed, err);
    }

    /// <summary>
    /// Win10 1607+ fast path: one handle open + NtSetInformationFile(FileDispositionInformationEx)
    /// with DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE. The file is deleted on close.
    /// Falls back (returns false) on ERROR_NOT_SUPPORTED / ERROR_INVALID_PARAMETER so the caller
    /// can use the classic path.
    /// </summary>
    public static bool TryDeleteFileDispositionEx(string prefixedPath, uint attributes, out int error)
    {
        error = 0;
        using var handle = Win32.CreateFileW(
            prefixedPath,
            Win32.DELETE,
            Win32.FILE_SHARE_READ | Win32.FILE_SHARE_WRITE | Win32.FILE_SHARE_DELETE,
            IntPtr.Zero,
            Win32.OPEN_EXISTING,
            Win32.FILE_FLAG_BACKUP_SEMANTICS | Win32.FILE_FLAG_OPEN_REPARSE_POINT | Win32.FILE_FLAG_POSIX_SEMANTICS,
            IntPtr.Zero);

        if (handle.IsInvalid)
        {
            error = Marshal.GetLastWin32Error();
            return false;
        }

        var info = new Win32.FILE_DISPOSITION_INFO_EX
        {
            Flags = Win32.FILE_DISPOSITION_DELETE
                  | Win32.FILE_DISPOSITION_POSIX_SEMANTICS
                  | Win32.FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE
        };

        int status = Win32.NtSetInformationFile(
            handle,
            out _,
            ref info,
            Marshal.SizeOf<Win32.FILE_DISPOSITION_INFO_EX>(),
            Win32.FileDispositionInformationEx);

        if (status < 0) // NTSTATUS: high bit set = error
        {
            error = (int)Win32.RtlNtStatusToDosError(status);
            // Not supported (downlevel Windows / exotic FS) - caller falls back.
            return false;
        }

        return true; // deleted on handle close
    }

    public static (DeleteStatus status, int error) DeleteDirectoryClassic(string prefixedPath)
    {
        if (Win32.RemoveDirectoryW(prefixedPath))
            return (DeleteStatus.Deleted, 0);

        int err = Marshal.GetLastWin32Error();
        return err switch
        {
            Win32.ERROR_FILE_NOT_FOUND or Win32.ERROR_PATH_NOT_FOUND => (DeleteStatus.NotFound, err),
            Win32.ERROR_DIR_NOT_EMPTY => (DeleteStatus.DirectoryNotEmpty, err),
            Win32.ERROR_ACCESS_DENIED => TryClearReadOnlyAndRetryDir(prefixedPath),
            _ => (DeleteStatus.Failed, err),
        };
    }

    private static (DeleteStatus status, int error) TryClearReadOnlyAndRetryDir(string prefixedPath)
    {
        // Clear the read-only attribute on the directory and retry once.
        uint attrs = GetAttributes(prefixedPath);
        if (attrs != uint.MaxValue && Win32.IsReadOnly(attrs))
        {
            Win32.SetFileAttributesW(prefixedPath, attrs & ~Win32.FILE_ATTRIBUTE_READONLY);
        }
        if (Win32.RemoveDirectoryW(prefixedPath))
            return (DeleteStatus.Deleted, 0);

        int err = Marshal.GetLastWin32Error();
        return err switch
        {
            Win32.ERROR_FILE_NOT_FOUND or Win32.ERROR_PATH_NOT_FOUND => (DeleteStatus.NotFound, err),
            Win32.ERROR_DIR_NOT_EMPTY => (DeleteStatus.DirectoryNotEmpty, err),
            _ => (DeleteStatus.Failed, err),
        };
    }

    /// <summary>
    /// Deletes a junction / symlink / mount point via FSCTL_DELETE_REPARSE_POINT, then
    /// RemoveDirectory for dir-links or DeleteFile for file-links. The target is never touched.
    /// </summary>
    public static (DeleteStatus status, int error) DeleteReparseLink(string prefixedPath, uint attributes)
    {
        bool isDir = Win32.IsDirectory(attributes);
        using var handle = Win32.CreateFileW(
            prefixedPath,
            Win32.GENERIC_WRITE,
            Win32.FILE_SHARE_READ | Win32.FILE_SHARE_WRITE | Win32.FILE_SHARE_DELETE,
            IntPtr.Zero,
            Win32.OPEN_EXISTING,
            Win32.FILE_FLAG_BACKUP_SEMANTICS | Win32.FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);

        if (handle.IsInvalid)
        {
            int openErr = Marshal.GetLastWin32Error();
            // Fall back to plain removal - still never follows the link.
            return isDir ? DeleteDirectoryClassic(prefixedPath) : DeleteFileClassic(prefixedPath);
        }

        var header = new byte[Win32.REPARSE_DATA_BUFFER_HEADER_SIZE];
        BitConverter.GetBytes(0u).CopyTo(header, 4); // ReparseDataLength = 0
        var gc = System.Runtime.InteropServices.GCHandle.Alloc(header, System.Runtime.InteropServices.GCHandleType.Pinned);
        try
        {
            bool ok = Win32.DeviceIoControl(
                handle,
                Win32.FSCTL_DELETE_REPARSE_POINT,
                gc.AddrOfPinnedObject(),
                header.Length,
                IntPtr.Zero,
                0,
                out _,
                IntPtr.Zero);
            if (!ok)
            {
                int err = Marshal.GetLastWin32Error();
                if (err is not (Win32.ERROR_NOT_SUPPORTED or Win32.ERROR_INVALID_PARAMETER or Win32.ERROR_INVALID_FUNCTION))
                    return (DeleteStatus.Failed, err);
                // FS didn't accept the delete reparse; plain removal still won't follow it.
            }
        }
        finally
        {
            gc.Free();
        }

        return isDir ? DeleteDirectoryClassic(prefixedPath) : DeleteFileClassic(prefixedPath);
    }
}
