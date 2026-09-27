using FastDelete.Core.Deletion;
using Xunit;

namespace FastDelete.Core.Tests;

public class DeletionEngineTests : IDisposable
{
    private readonly List<string> _cleanup = new();

    public void Dispose()
    {
        foreach (var dir in _cleanup)
        {
            try { if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true); }
            catch { /* best-effort cleanup */ }
        }
    }

    private string NewRoot([System.Runtime.CompilerServices.CallerMemberName] string name = "")
    {
        var root = TestTree.CreateTempRoot(name);
        _cleanup.Add(root);
        return root;
    }

    private static DeletionEngine Engine(int workers = 4, bool dispositionEx = true)
        => new(new DeletionOptions { MaxDegreeOfParallelism = workers, UseDispositionEx = dispositionEx });

    [Fact]
    public async Task Deletes_tree_with_files_and_directories()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 50, 10);

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(500, result.FilesDeleted);
        Assert.Equal(11, result.DirectoriesDeleted); // 10 subdirs + root
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Deletes_only_selected_items_mixed_files_and_folders()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 3, 2);
        string keepDir = Path.Combine(root, "keepdir");
        Directory.CreateDirectory(keepDir);
        string keepFile = Path.Combine(root, "keep.txt");
        File.WriteAllText(keepFile, "keep");
        string delFile = Path.Combine(root, "dir0", "f0_0.txt");
        string delDir = Path.Combine(root, "dir1");

        var result = await Engine().DeleteAsync(new[] { delFile, delDir });

        Assert.Empty(result.Failures);
        Assert.False(File.Exists(delFile));
        Assert.False(Directory.Exists(delDir));
        Assert.True(Directory.Exists(keepDir));
        Assert.True(File.Exists(keepFile));
        Assert.True(Directory.Exists(root));
    }

    [Fact]
    public async Task Junction_is_deleted_but_target_is_untouched()
    {
        string root = NewRoot();
        string outside = NewRoot();
        File.WriteAllText(Path.Combine(outside, "precious.txt"), "do not delete");

        TestTree.MakeFiles(root, 10, 1);
        string junction = Path.Combine(root, "linkout");
        TestTree.CreateJunction(junction, outside);

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(10, result.FilesDeleted);
        Assert.Equal(2, result.DirectoriesDeleted); // dir0 + root (junction counts as link)
        Assert.Equal(1, result.LinksDeleted);
        Assert.Empty(result.Failures);
        Assert.True(File.Exists(Path.Combine(outside, "precious.txt")), "Junction target must survive");
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Symlinks_are_deleted_but_targets_survive()
    {
        string root = NewRoot();
        string outside = NewRoot();
        string targetFile = Path.Combine(outside, "target.txt");
        string targetDir = Path.Combine(outside, "targetdir");
        File.WriteAllText(targetFile, "x");
        Directory.CreateDirectory(targetDir);
        File.WriteAllText(Path.Combine(targetDir, "inner.txt"), "x");

        string fileLink = Path.Combine(root, "filelink");
        string dirLink = Path.Combine(root, "dirlink");
        Directory.CreateDirectory(root);
        try
        {
            TestTree.CreateSymlink(dirLink, targetDir, directory: true);
            TestTree.CreateSymlink(fileLink, targetFile, directory: false);
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("1314"))
        {
            return; // no SeCreateSymbolicLinkPrivilege in this environment; junction tests cover the reparse path
        }

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(2, result.LinksDeleted);
        Assert.Empty(result.Failures);
        Assert.True(File.Exists(targetFile));
        Assert.True(File.Exists(Path.Combine(targetDir, "inner.txt")));
    }

    [Fact]
    public async Task Readonly_files_are_deleted()
    {
        string root = NewRoot();
        var file = Path.Combine(root, "ro.txt");
        Directory.CreateDirectory(root);
        File.WriteAllText(file, "x");
        File.SetAttributes(file, FileAttributes.ReadOnly);

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(1, result.FilesDeleted);
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Readonly_files_are_deleted_classic()
    {
        string root = NewRoot();
        var file = Path.Combine(root, "ro.txt");
        Directory.CreateDirectory(root);
        File.WriteAllText(file, "x");
        File.SetAttributes(file, FileAttributes.ReadOnly);

        var result = await Engine(dispositionEx: false).DeleteAsync(new[] { root });

        Assert.Equal(1, result.FilesDeleted);
        Assert.Empty(result.Failures);
    }

    [Fact]
    public async Task Locked_file_fails_but_run_continues()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 20, 1);
        var locked = Path.Combine(root, "dir0", "locked.txt");
        File.WriteAllText(locked, "x");
        using var fs = new FileStream(locked, FileMode.Open, FileAccess.ReadWrite, FileShare.None);

        // Classic strategy: locked file fails, engine continues with the rest.
        var result = await Engine(dispositionEx: false).DeleteAsync(new[] { root });

        fs.Close();
        Assert.Equal(20, result.FilesDeleted);
        // locked.txt (SHARING_VIOLATION), then dir0 and root fail with DIR_NOT_EMPTY
        Assert.Equal(3, result.Failures.Count);
        Assert.Contains(result.Failures, f => f.Path.EndsWith("locked.txt", StringComparison.OrdinalIgnoreCase));
        Assert.Contains(result.Failures, f => f.Path.EndsWith("dir0", StringComparison.OrdinalIgnoreCase));
        Assert.Contains(result.Failures, f => f.Path.EndsWith("root_", StringComparison.OrdinalIgnoreCase) || f.Path.Equals(root, StringComparison.OrdinalIgnoreCase));
        Assert.All(result.Failures, f => Assert.True(f.ErrorCode != 0));
    }

    [Fact]
    public async Task Locked_file_with_delete_share_is_unlinked_under_posix_semantics()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 10, 1);
        var locked = Path.Combine(root, "dir0", "posixlocked.txt");
        File.WriteAllText(locked, "x");
        // Opened with FileShare.Delete: DELETE access can be granted on the handle.
        using var fs = new FileStream(locked, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete);

        // FileDispositionInfoEx with POSIX_SEMANTICS unlinks the name even while open.
        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Empty(result.Failures);
        Assert.Equal(11, result.FilesDeleted);
        Assert.False(File.Exists(locked));
    }

    [Fact]
    public async Task Nonexistent_path_records_failure_without_throwing()
    {
        string root = NewRoot();
        string missing = Path.Combine(root, "does-not-exist");

        var result = await Engine().DeleteAsync(new[] { missing, root });

        Assert.Single(result.Failures);
        Assert.Contains(result.Failures, f => f.Path.Contains("does-not-exist", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public async Task Long_paths_beyond_MAX_PATH_work()
    {
        string root = NewRoot();
        // Build a path deeper than 260 chars
        string deep = root;
        for (int i = 0; i < 30; i++)
        {
            deep = Path.Combine(deep, "segment_" + i.ToString("D2") + "_with_a_fairly_long_name");
            Directory.CreateDirectory(deep);
        }
        File.WriteAllText(Path.Combine(deep, "deep.txt"), "x");
        Assert.True((root.Length + 30 * 40) > 260);

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(1, result.FilesDeleted);
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Channel_backpressure_tree_larger_than_channel_is_fully_deleted()
    {
        string root = NewRoot();
        // Default channel capacity is 16384 - this tree exceeds it, so the walker
        // blocks on WriteAsync instead of dropping items (regression test).
        TestTree.MakeFiles(root, 100, 200); // 20,000 files + 200 dirs + root

        var result = await Engine(workers: 4).DeleteAsync(new[] { root });

        Assert.Equal(20_000, result.FilesDeleted);
        Assert.Equal(201, result.DirectoriesDeleted);
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Many_small_files_scale_test()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 1000, 10); // 10,000 files

        var result = await Engine(workers: 4).DeleteAsync(new[] { root });

        Assert.Equal(10_000, result.FilesDeleted);
        Assert.Equal(11, result.DirectoriesDeleted);
        Assert.Empty(result.Failures);
        Assert.True(result.ItemsPerSecond > 500, $"Expected >500 items/s, got {result.ItemsPerSecond:F0}");
    }

    [Fact]
    public async Task Sequential_and_parallel_produce_same_results()
    {
        string rootA = NewRoot();
        string rootB = NewRoot();
        TestTree.MakeFiles(rootA, 200, 5);
        TestTree.MakeFiles(rootB, 200, 5);

        var seq = await Engine(workers: 1).DeleteAsync(new[] { rootA });
        var par = await Engine(workers: 8).DeleteAsync(new[] { rootB });

        Assert.Equal(1000, seq.FilesDeleted);
        Assert.Equal(1000, par.FilesDeleted);
        Assert.Equal(seq.DirectoriesDeleted, par.DirectoriesDeleted);
        Assert.Empty(seq.Failures);
        Assert.Empty(par.Failures);
    }

    [Fact]
    public async Task Cancellation_stops_run_and_reports_it()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 2000, 20); // 40,000 files
        using var cts = new CancellationTokenSource(1500);

        var result = await Engine(workers: 4).DeleteAsync(new[] { root }, cancellationToken: cts.Token);

        Assert.True(result.WasCancelled);
        Assert.True(result.ItemsProcessed < 40_000);
    }

    [Fact]
    public async Task Pause_and_resume_completes()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 100, 2);
        var pause = new PauseToken();
        pause.Pause();

        var task = Engine().DeleteAsync(new[] { root }, pauseToken: pause);
        await Task.Delay(300);
        Assert.False(task.IsCompleted);

        pause.Resume();
        var result = await task;

        Assert.Equal(200, result.FilesDeleted);
        Assert.Empty(result.Failures);
    }

    [Fact]
    public async Task Progress_reports_counters()
    {
        string root = NewRoot();
        TestTree.MakeFiles(root, 10, 1);
        var snapshots = new List<DeletionProgress>();
        var progress = new Progress<DeletionProgress>(p => snapshots.Add(p));

        await Engine(workers: 1).DeleteAsync(new[] { root }, progress);

        Assert.NotEmpty(snapshots);
        Assert.Contains(snapshots, p => p.FilesDeleted == 10);
        Assert.Contains(snapshots, p => p.Elapsed > TimeSpan.Zero);
    }

    [Fact]
    public async Task Empty_tree_root_is_deleted()
    {
        string root = NewRoot();
        Directory.CreateDirectory(Path.Combine(root, "empty"));

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(2, result.DirectoriesDeleted); // empty + root
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }

    [Fact]
    public async Task Unicode_and_special_names_are_handled()
    {
        string root = NewRoot();
        string sub = Path.Combine(root, "Ünïcødé 目录 🎒");
        Directory.CreateDirectory(sub);
        File.WriteAllText(Path.Combine(sub, "файл ファイル.txt"), "x");
        File.WriteAllText(Path.Combine(sub, "with trailing space .txt"), "x");
        File.WriteAllText(Path.Combine(root, "..dots.."), "x");

        var result = await Engine().DeleteAsync(new[] { root });

        Assert.Equal(3, result.FilesDeleted);
        Assert.Empty(result.Failures);
        Assert.False(Directory.Exists(root));
    }
}
