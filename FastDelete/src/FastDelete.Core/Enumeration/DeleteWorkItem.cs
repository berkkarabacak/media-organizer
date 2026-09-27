namespace FastDelete.Core.Enumeration;

public enum WorkItemKind : byte
{
    File,
    Directory,
    ReparseLink,
}

/// <summary>One unit of deletion work, streamed from the walker to the engine.</summary>
public readonly struct DeleteWorkItem
{
    public string Path { get; }        // \\?\ -prefixed
    public WorkItemKind Kind { get; }
    public uint Attributes { get; }

    public DeleteWorkItem(string path, WorkItemKind kind, uint attributes)
    {
        Path = path;
        Kind = kind;
        Attributes = attributes;
    }
}
