// 32-bit host for the game's PhysX 2.8.1 cooking library.
//
// DAO's nxcooking.dll is 32-bit, Blender is 64-bit: run this as a subprocess.
//
// Build:
//   %WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe /nologo /platform:x86
//       /out:cook.exe cook.cs
//
// Run:
//   cook <nxcooking.dll> <mesh.txt> <out.bin> [edge] [axis] [extent]
//
// mesh.txt: "numVerts numTris", then vertices, then triangles. The trailing
// numbers are DAO's cooked-header values: convexEdgeThreshold,
// heightFieldVerticalAxis (0xff = not a height field), vertical extent.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;

static class Cook
{
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string path);

    [DllImport("kernel32", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate bool Init2(IntPtr allocator, IntPtr outputStream);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate bool CookFn(IntPtr desc, IntPtr stream);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate void V0(IntPtr self);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate int R0(IntPtr self);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate void R2(IntPtr self, IntPtr buffer, int size);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate IntPtr W1(IntPtr self, int value);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate IntPtr W1d(IntPtr self, double value);

    [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
    delegate IntPtr W2(IntPtr self, IntPtr buffer, int size);

    static List<byte> buffer = new List<byte>();

    static IntPtr MakeObject(params Delegate[] methods)
    {
        IntPtr vtable = Marshal.AllocHGlobal(methods.Length * IntPtr.Size);
        for (int i = 0; i < methods.Length; i++)
            Marshal.WriteIntPtr(vtable, i * IntPtr.Size,
                Marshal.GetFunctionPointerForDelegate(methods[i]));
        IntPtr obj = Marshal.AllocHGlobal(IntPtr.Size);
        Marshal.WriteIntPtr(obj, 0, vtable);
        return obj;
    }

    static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("usage: cook <nxcooking.dll> <mesh.txt> <out.bin>");
            return 2;
        }
        string dll = args[0], meshFile = args[1], outFile = args[2];
        float edge = 0.001f, extent = 0.0f;
        int axis = 0xFF;
        bool okParse = true;
        if (args.Length > 3) okParse &= float.TryParse(args[3], NumberStyles.Float,
            CultureInfo.InvariantCulture, out edge);
        if (args.Length > 4) okParse &= int.TryParse(args[4], out axis);
        if (args.Length > 5) okParse &= float.TryParse(args[5], NumberStyles.Float,
            CultureInfo.InvariantCulture, out extent);
        if (!okParse)
        {
            Console.Error.WriteLine("bad cooking parameters");
            return 2;
        }

        string[] lines = File.ReadAllLines(meshFile);
        string[] head = lines[0].Split(' ');
        int numVerts = int.Parse(head[0], CultureInfo.InvariantCulture);
        int numTris = int.Parse(head[1], CultureInfo.InvariantCulture);
        float[] verts = new float[numVerts * 3];
        int[] tris = new int[numTris * 3];
        for (int i = 0; i < numVerts; i++)
        {
            string[] parts = lines[1 + i].Split(' ');
            for (int k = 0; k < 3; k++)
                verts[i * 3 + k] = float.Parse(parts[k], CultureInfo.InvariantCulture);
        }
        for (int i = 0; i < numTris; i++)
        {
            string[] parts = lines[1 + numVerts + i].Split(' ');
            for (int k = 0; k < 3; k++)
                tris[i * 3 + k] = int.Parse(parts[k], CultureInfo.InvariantCulture);
        }

        IntPtr module = LoadLibraryW(dll);
        if (module == IntPtr.Zero)
        {
            Console.Error.WriteLine("cannot load " + dll + " (" + Marshal.GetLastWin32Error() + ")");
            return 1;
        }
        IntPtr pInit = GetProcAddress(module, "NxInitCooking");
        IntPtr pCook = GetProcAddress(module, "NxCookTriangleMesh");
        if (pInit == IntPtr.Zero || pCook == IntPtr.Zero)
        {
            Console.Error.WriteLine("not a PhysX 2.x cooking library");
            return 1;
        }

        // default allocator and error handler are fine
        bool started = ((Init2)Marshal.GetDelegateForFunctionPointer(pInit, typeof(Init2)))(
            IntPtr.Zero, IntPtr.Zero);
        if (!started)
        {
            Console.Error.WriteLine("NxInitCooking failed");
            return 1;
        }

        IntPtr vbuf = Marshal.AllocHGlobal(verts.Length * 4);
        Marshal.Copy(verts, 0, vbuf, verts.Length);
        IntPtr tbuf = Marshal.AllocHGlobal(tris.Length * 4);
        Marshal.Copy(tris, 0, tbuf, tris.Length);

        // NxTriangleMeshDesc: base fields, height-field axis/extent, then the edge threshold
        IntPtr desc = Marshal.AllocHGlobal(64);
        for (int i = 0; i < 64; i++) Marshal.WriteByte(desc, i, 0);
        Marshal.WriteInt32(desc, 0, numVerts);
        Marshal.WriteInt32(desc, 4, numTris);
        Marshal.WriteInt32(desc, 8, 12);    // pointStrideBytes
        Marshal.WriteInt32(desc, 12, 12);   // triangleStrideBytes
        Marshal.WriteIntPtr(desc, 16, vbuf);
        Marshal.WriteIntPtr(desc, 20, tbuf);
        Marshal.WriteInt32(desc, 24, 0);    // flags: 32-bit indices
        Marshal.WriteInt32(desc, 36, axis);
        Marshal.WriteInt32(desc, 40, BitConverter.ToInt32(BitConverter.GetBytes(extent), 0));
        Marshal.WriteInt32(desc, 48, BitConverter.ToInt32(BitConverter.GetBytes(edge), 0));

        // NxStream: dtor, six readers, six writers, in SDK order
        Delegate[] stream = new Delegate[13];
        stream[0] = new V0(delegate(IntPtr s) { });
        stream[1] = new R0(delegate(IntPtr s) { return 0; });
        stream[2] = new R0(delegate(IntPtr s) { return 0; });
        stream[3] = new R0(delegate(IntPtr s) { return 0; });
        stream[4] = new R0(delegate(IntPtr s) { return 0; });
        stream[5] = new R0(delegate(IntPtr s) { return 0; });
        stream[6] = new R2(delegate(IntPtr s, IntPtr b, int n) { });
        stream[7] = new W1(delegate(IntPtr s, int v)
        { buffer.Add((byte)(v & 0xFF)); return s; });
        stream[8] = new W1(delegate(IntPtr s, int v)
        { buffer.Add((byte)(v & 0xFF)); buffer.Add((byte)((v >> 8) & 0xFF)); return s; });
        stream[9] = new W1(delegate(IntPtr s, int v)
        { buffer.AddRange(BitConverter.GetBytes(v)); return s; });
        stream[10] = new W1(delegate(IntPtr s, int v)
        { buffer.AddRange(BitConverter.GetBytes(v)); return s; });
        stream[11] = new W1d(delegate(IntPtr s, double v)
        { buffer.AddRange(BitConverter.GetBytes(v)); return s; });
        stream[12] = new W2(delegate(IntPtr s, IntPtr b, int n)
        {
            if (n > 0 && b != IntPtr.Zero)
            {
                byte[] tmp = new byte[n];
                Marshal.Copy(b, tmp, 0, n);
                buffer.AddRange(tmp);
            }
            return s;
        });
        IntPtr streamObj = MakeObject(stream);

        bool cooked = ((CookFn)Marshal.GetDelegateForFunctionPointer(pCook, typeof(CookFn)))(
            desc, streamObj);
        if (!cooked || buffer.Count == 0)
        {
            Console.Error.WriteLine("NxCookTriangleMesh failed");
            return 1;
        }
        File.WriteAllBytes(outFile, buffer.ToArray());
        return 0;
    }
}
