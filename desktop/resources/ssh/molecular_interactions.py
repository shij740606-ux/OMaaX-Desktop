"""OMaaX interaction analysis. Reads prepared coordinates; never generates docking poses."""
import contextlib, hashlib, io, json, math, sys, tempfile
from pathlib import Path

def analyze(body):
    import MDAnalysis as mda
    import prolif as plf
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
    protein = body.get("protein", "")
    ligand = body.get("ligand", "")
    if not isinstance(protein, str) or not isinstance(ligand, str) or len(protein.encode()) > 4_000_000 or len(ligand.encode()) > 1_000_000:
        raise ValueError("输入过大：蛋白最多 4 MB，配体最多 1 MB。请只选择所需结构。")
    if not protein.strip() or not ligand.strip():
        raise ValueError("请导入已加氢的蛋白 PDB 和含真实三维坐标的配体 SDF。")
    with tempfile.TemporaryDirectory(prefix="omaax-interactions-") as directory:
        u = mda.Universe(io.StringIO(protein), format="PDB")
        if len(u.atoms) > 60000: raise ValueError("结构过大，请提取所需蛋白链再分析。")
        # Respect protonation in the input; guessing hydrogens could alter the science.
        if not any(str(e).upper() == "H" for e in u.atoms.elements):
            raise ValueError("蛋白缺少显式氢原子。请先完成结构加氢和质子化，再分析相互作用。原文件未修改。")
        prot = plf.Molecule.from_mda(u)
        molecules = list(Chem.ForwardSDMolSupplier(io.BytesIO(ligand.encode()), removeHs=False))
        if not molecules or len(molecules) > 50:
            raise ValueError("请提供 1–50 个 SDF 对接姿势。")
        fp = plf.Fingerprint(count=True)
        poses = []
        for index, mol in enumerate(molecules):
            if mol is None: raise ValueError("第 %d 个姿势无法读取，请检查 SDF 键级和结构。" % (index+1))
            if mol.GetNumAtoms() > 500: raise ValueError("配体超过 500 个原子，请缩小输入。")
            if not mol.GetNumConformers() or not mol.GetConformer().Is3D():
                raise ValueError("第 %d 个姿势不是三维结构，不能作为对接结果分析。" % (index+1))
            if not any(a.GetAtomicNum() == 1 for a in mol.GetAtoms()):
                raise ValueError("第 %d 个配体缺少显式氢原子，请先加氢并保留对接坐标。" % (index+1))
            coords = mol.GetConformer().GetPositions()
            if not all(math.isfinite(float(v)) for row in coords for v in row):
                raise ValueError("结构坐标包含无效数值。")
            fingerprint = fp.generate(plf.Molecule(mol), prot, metadata=True)
            rows = []
            for pair, interactions in fingerprint.items():
                for kind, entries in interactions.items():
                    for entry in entries:
                        distance = entry.get("distance")
                        rows.append({"ligand":str(pair[0]), "residue":str(pair[1]), "type":str(kind),
                                     "distance":float(distance) if distance is not None else None,
                                     "atoms":entry.get("parent_indices", {})})
            # Depiction uses a separate copy. The original 3D coordinates stay untouched.
            picture = Chem.RemoveHs(Chem.Mol(mol))
            rdDepictor.Compute2DCoords(picture)
            drawer = rdMolDraw2D.MolDraw2DSVG(420, 300)
            drawer.DrawMolecule(picture)
            drawer.FinishDrawing()
            poses.append({"index":index, "name":(mol.GetProp("_Name").strip() if mol.HasProp("_Name") else "") or "姿势 %d" % (index+1),
                          "rows":rows, "ligandSvg":drawer.GetDrawingText()})
        return {"format":"omaax-interactions", "version":1, "engine":"ProLIF", "engineVersion":plf.__version__,
                "proteinSha256":hashlib.sha256(protein.encode()).hexdigest(),
                "ligandSha256":hashlib.sha256(ligand.encode()).hexdigest(),
                "poses":poses, "note":"按输入结构、质子化状态与 ProLIF 几何规则识别；不代表结合亲和力或实验验证。"}

def main():
    try:
        body = json.loads(sys.stdin.read(5_100_000))
        with contextlib.redirect_stdout(sys.stderr):
            result = analyze(body)
        output=json.dumps(result, ensure_ascii=False, default=lambda value: value.item() if hasattr(value, "item") else list(value))
        if body.get('compress'):
            import gzip, base64
            output=json.dumps({'compressed':base64.b64encode(gzip.compress(output.encode('utf-8'))).decode('ascii')})
        print(output)
    except Exception as exc:
        print(json.dumps({"error":str(exc)[:1800]}, ensure_ascii=False))
        sys.exit(1)
if __name__ == "__main__":
    main()

