using System;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;

class T {
    static void Main(string[] args) {
        string sdkDir = args[0];
        AssemblyLoadContext.Default.Resolving += (ctx, name) => {
            string p = Path.Combine(sdkDir, name.Name + ".dll");
            return File.Exists(p) ? ctx.LoadFromAssemblyPath(p) : null;
        };
        var asm = AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(sdkDir, "NuGet.Common.dll"));
        var envType = asm.GetType("NuGet.Common.NuGetEnvironment");
        var folderType = asm.GetType("NuGet.Common.NuGetFolderPath");
        var getFolder = envType.GetMethod("GetFolderPath", new[] { folderType });
        foreach (var val in Enum.GetValues(folderType)) {
            try {
                var r = getFolder.Invoke(null, new object[] { val });
                Console.WriteLine(val + " = [" + r + "]");
            } catch (Exception ex) {
                Console.WriteLine(val + " = THROWS: " + ex.InnerException?.GetType().Name + ": " + ex.InnerException?.Message);
            }
        }
    }
}
