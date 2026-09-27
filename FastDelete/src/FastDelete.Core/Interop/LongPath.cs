namespace FastDelete.Core.Interop;

/// <summary>\\?\ long-path helpers. Every engine API receives a prefixed path;
/// every user-visible string has the prefix stripped.</summary>
public static class LongPath
{
    public const string PrefixString = @"\\?\";

    /// <summary>Converts a normal or \\?\ path to the \\?\ form (no normalization).
    /// UNC paths become \\?\UNC\server\share.</summary>
    public static string Prefix(string path)
    {
        if (path.StartsWith(PrefixString, StringComparison.Ordinal))
            return path;
        if (path.StartsWith(@"\\", StringComparison.Ordinal))
            return @"\\?\UNC\" + path[2..];
        return PrefixString + path;
    }

    /// <summary>Joins a file name onto an already-prefixed directory path.</summary>
    public static string Join(string prefixedDir, string name)
        => prefixedDir.TrimEnd('\\') + "\\" + name;

    /// <summary>User-display form of a possibly-prefixed path.</summary>
    public static string Display(string path)
    {
        if (path.StartsWith(PrefixString, StringComparison.Ordinal))
            return path[PrefixString.Length..];
        return path;
    }

    /// <summary>Case-insensitive canonical form used for duplicate/canonical comparisons.</summary>
    public static string NormalizeForCompare(string path)
    {
        var p = Display(path).TrimEnd('\\');
        return p.ToUpperInvariant();
    }
}
