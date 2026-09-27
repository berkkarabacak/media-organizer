using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace FastDelete.App;

public partial class App : System.Windows.Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        // Software rendering: WPF defaults to hardware (WARP) tier 1/2 even on GPU-less
        // VMs, which produced blank-window artifacts there; software is plenty for a
        // file-manager UI and keeps rendering identical across machines.
        RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;

        DispatcherUnhandledException += OnDispatcherUnhandledException;
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
            Console.Error.WriteLine($"[AppDomain unhandled] {args.ExceptionObject}");
        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            Console.Error.WriteLine($"[Unobserved task exception] {args.Exception}");
            args.SetObserved();
        };

        var window = new Views.MainWindow();
        // Command line: FastDelete.exe [folder] - opens that folder directly
        // (intended for an Explorer "Open in FastDelete" entry / Send-To shortcut).
        if (e.Args.Length > 0 && System.IO.Directory.Exists(e.Args[0]))
        {
            ((ViewModels.MainViewModel)window.DataContext)
                .NavigateCommand.Execute(System.IO.Path.GetFullPath(e.Args[0]));
        }
        window.Show();
    }

    private void OnDispatcherUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        var line = $"[{DateTime.Now:HH:mm:ss}] {e.Exception}";
        Console.Error.WriteLine(line);
        try { System.IO.File.AppendAllText(System.IO.Path.Combine(System.IO.Path.GetTempPath(), "fastdelete-exceptions.log"), line + Environment.NewLine); }
        catch { /* diagnostics must never themselves crash */ }
        // A render/binding failure must not silently kill the window surface.
        e.Handled = true;
    }
}
