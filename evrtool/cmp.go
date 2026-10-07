package main

import (
	"bytes"
	"fmt"
	"path/filepath"

	"github.com/EchoTools/cosmetic-editor/EvrFile/manifest"
)

// cmpOldNew compares every file of the old install (manifest + delta _3 kept in
// oldDir) against the live install, using the stock packages for frames < 3.
func cmpOldNew(dataDir, oldDir string) {
	mp := filepath.Join(dataDir, "manifests", pkg)
	cur, err := manifest.ReadFile(mp)
	if err != nil { panic(err) }
	old, err := manifest.ReadFile(filepath.Join(oldDir, "manifest_old"))
	if err != nil { panic(err) }
	pc, err := manifest.OpenPackage(cur, filepath.Join(dataDir, "packages", pkg))
	if err != nil { panic(err) }
	po, err := manifest.OpenPackage(old, filepath.Join(oldDir, pkg))
	if err != nil { panic(err) }
	ps, err := manifest.OpenPackage(old, filepath.Join(dataDir, "packages", pkg))
	if err != nil { panic(err) }
	idx := map[[2]int64]int{}
	for i, fc := range cur.FrameContents { idx[[2]int64{fc.TypeSymbol, fc.FileSymbol}] = i }
	nd, nm := 0, 0
	for _, fc := range old.FrameContents {
		k := [2]int64{fc.TypeSymbol, fc.FileSymbol}
		ci, ok := idx[k]
		if !ok { fmt.Printf("MISSING %016x/%016x\n", uint64(k[0]), uint64(k[1])); nm++; continue }
		oldPkg := old.Frames[fc.FrameIndex].PackageIndex
		newPkg := cur.Frames[cur.FrameContents[ci].FrameIndex].PackageIndex
		if oldPkg < 3 && newPkg < 3 { continue }
		ofc := fc
		var a []byte
		if oldPkg >= 3 { a, err = po.ReadContent(&ofc) } else { a, err = ps.ReadContent(&ofc) }
		if err != nil { fmt.Println("ERROLD", err); continue }
		nfc := cur.FrameContents[ci]
		b, err := pc.ReadContent(&nfc)
		if err != nil { fmt.Println("ERRNEW", err); continue }
		if !bytes.Equal(a, b) { fmt.Printf("DIFF %016x/%016x %d %d\n", uint64(k[0]), uint64(k[1]), len(a), len(b)); nd++ }
	}
	fmt.Println("old files", len(old.FrameContents), "new files", len(cur.FrameContents), "diff", nd, "missing", nm)
}
