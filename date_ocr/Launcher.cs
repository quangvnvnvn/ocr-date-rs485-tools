using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class OCRDateLauncher {
 [STAThread] static void Main() {
  try {
   string root=AppDomain.CurrentDomain.BaseDirectory;
   string python=File.ReadAllText(Path.Combine(root,"python-runtime.txt")).Trim();
   string script=Path.Combine(root,"gui.py");
   if(!File.Exists(python) || !File.Exists(script)) throw new Exception("Khong tim thay Python hoac chuong trinh OCR. Vui long kiem tra thu muc cai dat.");
   var start=new ProcessStartInfo(python,"\""+script+"\"");
   start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;
   start.EnvironmentVariables["PYTHONUTF8"]="1";
   Process.Start(start);
  } catch(Exception ex) {MessageBox.Show(ex.Message,"OCR DATE",MessageBoxButtons.OK,MessageBoxIcon.Error);}
 }
}
