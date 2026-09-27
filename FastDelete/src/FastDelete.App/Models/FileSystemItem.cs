using CommunityToolkit.Mvvm.ComponentModel;
using System.Windows.Media;

namespace FastDelete.App.Models;

/// <summary>One row in the file list.</summary>
public partial class FileSystemItem : ObservableObject
{
    public required string Name { get; init; }
    public required string FullPath { get; init; }
    public bool IsDirectory { get; init; }
    public bool IsReparsePoint { get; init; }
    public long? Size { get; init; }            // null for directories
    public DateTime Modified { get; init; }
    public ImageSource? Icon { get; init; }

    public string TypeName => IsDirectory
        ? (IsReparsePoint ? "Shortcut link" : "Folder")
        : System.IO.Path.GetExtension(Name).TrimStart('.').ToUpperInvariant() is { Length: > 0 } ext ? ext + " File" : "File";

    public override string ToString() => Name;
}
