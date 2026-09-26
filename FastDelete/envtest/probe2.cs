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
        Console.WriteLine("in-proc ProgramData env = [" + Environment.GetEnvironmentVariable("ProgramData") + "]");
        Console.WriteLine("in-proc GetFolderPath(CommonApplicationData) = [" + Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData) + "]");
        foreach (var m in envType.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)) {
            if (m.Name != "GetFolderPath") continue;
            Console.WriteLine("method: " + m);
        }
        // try the private SpecialFolder overload
        foreach (var m in envType.GetMethods(BindingFlags.Static | BindingFlags.NonPublic)) {
            if (m.Name == "GetFolderPath" && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType.FullName.Contains("SpecialFolder") && !m.GetParameters()[0].ParameterType.FullName.Contains("NuGetFolderPath")) {
                var pType = m.GetParameters()[0].ParameterType;
                var cad = Enum.Parse(pType, "CommonApplicationData");
                try {
                    var r = m.Invoke(null, new object[] { cad });
                    Console.WriteLine("NuGet private GetFolderPath(" + pType.FullName + ".CommonApplicationData) = [" + r + "]");
                } catch (Exception ex) {
                    Console.WriteLine("THROWS " + ex.InnerException);
                }
            }
        }
    }
}
