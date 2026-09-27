using CommunityToolkit.Mvvm.ComponentModel;
using FastDelete.App.Services;
using System.Collections.ObjectModel;
using System.Windows.Media;

namespace FastDelete.App.Models;

/// <summary>Lazy tree node: children load on first expansion. A placeholder child
/// keeps the expand arrow visible without touching the disk.</summary>
public partial class TreeNode : ObservableObject
{
    public required string Name { get; init; }
    public required string FullPath { get; init; }
    public bool IsPlaceholder { get; private init; }
    public ImageSource? Icon { get; init; }

    public ObservableCollection<TreeNode> Children { get; } = new();

    [ObservableProperty]
    private bool _isExpanded;

    /// <summary>Real node with a lazy-loading placeholder child.</summary>
    public static TreeNode Create(string name, string fullPath, ImageSource? icon = null)
    {
        var node = new TreeNode { Name = name, FullPath = fullPath, Icon = icon };
        node.Children.Add(new TreeNode { Name = "…", FullPath = string.Empty, IsPlaceholder = true });
        return node;
    }

    partial void OnIsExpandedChanged(bool value)
    {
        if (value && Children.Count == 1 && Children[0].IsPlaceholder)
        {
            Children.Clear();
            foreach (var child in TreeService.LoadChildDirectories(FullPath))
                Children.Add(Create(child.Name, child.FullPath, IconService.GetIcon(child.FullPath, isDirectory: true)));
        }
    }

    public static TreeNode CreateRoot(string path)
    {
        string name = System.IO.Path.GetPathRoot(path)?.TrimEnd('\\') ?? path;
        var node = new TreeNode { Name = name, FullPath = path, Icon = IconService.GetIcon(path, isDirectory: true) };
        foreach (var child in TreeService.LoadChildDirectories(path))
            node.Children.Add(Create(child.Name, child.FullPath, IconService.GetIcon(child.FullPath, isDirectory: true)));
        return node;
    }
}
