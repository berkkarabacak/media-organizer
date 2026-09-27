namespace FastDelete.App.Themes;

/// <summary>
/// Dark/light theme swapper. Brushes are declared as <c>DynamicResource</c> keys in
/// Theme.xaml; switching replaces the dictionary at runtime.
/// </summary>
public static class ThemeManager
{
    private const string LightSource = "Themes/Theme.Light.xaml";
    private const string DarkSource = "Themes/Theme.Dark.xaml";

    public static bool IsDark { get; private set; }

    public static void Apply(bool dark)
    {
        IsDark = dark;
        var app = System.Windows.Application.Current;
        var old = app.Resources.MergedDictionaries.FirstOrDefault(d => d.Source != null &&
            (d.Source.OriginalString.EndsWith("Theme.Light.xaml") || d.Source.OriginalString.EndsWith("Theme.Dark.xaml")));
        if (old != null)
            app.Resources.MergedDictionaries.Remove(old);
        app.Resources.MergedDictionaries.Insert(0, new System.Windows.ResourceDictionary
        {
            Source = new Uri(dark ? DarkSource : LightSource, UriKind.Relative),
        });
    }
}
