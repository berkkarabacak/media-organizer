using System;
class T {
    static void Main() {
        foreach (Environment.SpecialFolder f in Enum.GetValues(typeof(Environment.SpecialFolder))) {
            string v = null;
            try { v = Environment.GetFolderPath(f); } catch (Exception ex) { Console.WriteLine(f + " = EXCEPTION " + ex.GetType().Name); continue; }
            if (string.IsNullOrEmpty(v)) Console.WriteLine(f + " = [" + (v == null ? "NULL" : "EMPTY") + "]");
        }
        Console.WriteLine("done");
    }
}
