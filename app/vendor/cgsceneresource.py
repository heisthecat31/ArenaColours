"""CGSceneResource (v3 Primary) — disassembly-confirmed CGSceneData member walk.

The Primary payload is `NRadEngine::CGSceneData::Serialize(CSerializer&)`
(Quest libr15.so build 3137595842946788 @0x227e3d8): a 22-member,
declaration-order walk. s37 disassembled the whole serializer tree; s38 closed
the HEAD crux and shipped this genuine count/size-driven decoder (replaces the
s29 704-byte constant template, 136/626).

Every member is a named primitive whose on-disk form is fully confirmed by disasm
of each `*::Serialize` (vtable slots: +0x90 = write u32 count, +0x88 = bulk record
bytes, +0x98/+0xa8 = begin/end-field tags that emit NO bytes):

  CTable<T> / CTableXT<T>      [u32 count][count * stride(T)]   (empty = [u32 0];
                              the bulk path is skipped when count==0 — cbz w8)
  CSimpleKeyTable<K,_,V>       CTable<CSimpleKey<K,V>> (combined key+value records)
                              + a single u32 CFlagsT  (empty = 8 bytes = the same
                              [u32 0 count][u32 0 flags]).  s43: disasm-corrected —
                              the bucket/hash index is rebuilt IN MEMORY on read
                              (CTable::GrowBy), NOT serialized; the s38 "two parallel
                              CTables" model fit only the empty form (see keytable()).
  CMemBlock                    [u32 size][size bytes]            (verbatim heap)
  CMemBlock (bulk, ivdata)     [u32 size][u64 attach][size bytes]
  inline scalar                u32        CBox  6*f32

THE HEAD CRUX (s37 §6) resolved: member 9 `shapelist` IS a `CMemBlock`
(`CMemBlock::Serialize` @0x1385210 in the member sequence) — the per-scene attach
heap that carries the baked node-graph table (the 24B lookup records + i64 relative
offsets + verts). It is carried VERBATIM, segmented by its own `[u32 size]` (exactly
like the mesh module carries CGMeshData/CGVertexFormat records). So the CGraphT
topology that stalled s36/s37 never needs byte-level decoding here: on disk it lives
inside the shapelist CMemBlock. Members 10-14 (shapelists/nodegraph/sound) are the
lightweight index tables; they are empty in the N=9 class and small in N=17/19.

The 22-member sequence (disasm order = on-disk order):
  1 lights 2 vlights 3 avolumes 4 dirlightdirs 5 dirlightindices  (CTable, empty)
  6 ivdata = bulk{ CTable + 4*CTable<CSymbol64> + 2*CMemBlock }     (empty 44B)
  7 particleeffects(CTable)  bonecount(u32)  boundingboxidx(u32)  8 actors(CTable)
  9 shapelist(CMemBlock)  10 shapelists(SShapeLists)  11 nodegraph(SNodeGraph)
  12 soundvolumelist 13 soundportallist 14 soundportalgrouplist
  15 materials(CTable<CSymbol64>) 16 shadersets(CTable<SGMeshShaderSet> st24)
  flags(u32) padding(u32)  17 boundingbox(CBox 6f)  18 sets(CGSceneSetsData)
  19 aovolumes 20 jointoffsets 21 boxtree(bulk) 22 debugdata(3*CTable<CSymbol64>)

Scope: byte-exact on 612/626 (s43, was 603). The version-head prefix (N fixed 360B
instance-header blocks; N=word[0], VHEAD_SIG @word[3]/[4]) is peeled verbatim by
_vhead_len() before the same member graph (s38 2nd pass).
s39 closed 3 sub-decodes (DWARF byte_size of the runtime struct = CTable bulk-copy stride,
+ disasm), lifting 591->603:
  (1) LEAD-member strides (lights/vlights/avolumes / ivdata.volumes / actors / 3 sound
      lists) pinned from DWARF (SGLightParams 360 etc.) — +5 small scenes.
  (2) CEDGE 16->8 (DWARF; nodegraph edges empty in per-prop so never exercised) — +4.
  (3) ivdata = 5 CTables + 4 plain CMemBlocks ([u32 size][bytes]) + an 8B bulk-scope
      close marker (disasm CGIrradianceVolumesData::Serialize; the s38 "2 bulk blobs
      w/ u64 attach" only fit the all-zero empty form) — +3.

s43 closed the CSimpleKeyTable non-empty form (603->612, +9 whole-map scenes). The s38
"two parallel CTables (keys + values)" model was byte-exact only for the EMPTY case
(both [u32 0]). Disasm of CSimpleKeyTable<u64,_,CSymbol64>::Serialize @0x3adecfc =
inner CTable<CSimpleKey<K,V>>::Serialize (this+0) + Serialize<u32,1>(CFlagsT&) (this+0x38);
the inner @0x3adee58 writes [u32 count] + count*stride records, then rebuilds the hash
bucket IN MEMORY (CTable::GrowBy — no stream bytes). So on disk a non-empty map is just
[u32 count][count*stride][u32 flags] (the s39 "[u32 1] bucket" after celebration's 311
records is the flags). CSimpleKey<u64,CSymbol64>=16 (probe-confirmed; the finding's "24B"
was a misread), <u32,u32>=8, <u32,CSymbol64>=16.

s44 closed the SShapeListT<SSplineData>/<CConvex> member-10 data tables (612->616, +4 files:
arena_a, lobby_b_arena, lobby_b_combat, tutorial_arena — the spline-bearing scenes with EMPTY
sound lists). Disasm: SShapeListT<T>::Serialize -> CTableXT<T>::Serialize (CSerializerEnableBulk
wrap, NO on-disk trailer) -> SerializeArray<T> -> T::Serialize, per element. SSplineData (232B)
= u32 + CTable<float>(knots) + CTable<C3Vector>(controlpoints) + u32 + CTable<C3Vector>(samples)
+ CTable<C3Vector>(derivatives) [the 2 u32 are the +0x40 inline scalars]. CConvex (80B) =
CTable<CPlane>(faces, stride 16) + 2 C3Vector — but CONVEX lists are EMPTY across the whole
shipped corpus, so its element body is disasm-derived and unexercised on disk.

s45 closed the member-12/13/14 SOUND lists (616->626, the last 10 files), so the scene type is
now COMPLETE. All three are structs-OF-CTables (per-element Serialize), NOT the flat PODs the
s39 DWARF byte_size implied (304/128/56 were RUNTIME sizes). See _soundvol_elem_read /
_portalgroup_elem_read + the stride-128 member-13 ctable:
  * member 12 soundvolumelist  CTableXT<SGSoundVolumeData> — 9-field element (name/nodeid/reverb/
    priority/portalprog/ambient/switches/volumes/connectiondata). The `volumes` CTableXT<CConvex>
    appends a +4 trailer PER CONVEX (the s44 §10 convex element, now stream-validated); the
    element itself has no trailer.
  * member 13 soundportallist  CTable<SGSoundPortalData> — FLAT stride 128 (124 fields + 4 trailer).
  * member 14 soundportalgrouplist CTableXT<SGSoundPortalGroupData> — element = CTable<u64>, no trailer.
The +4 (always 0) is written by the container per-element loop (SerializeArray, GOT-resolved);
empirically pinned + export-validated. Only CConvex/SGSoundPortalData carry it; SGSoundVolumeData/
SAmbientInfo/SSplineData do not.

Evidence: `DWARF-confirmed` (libr15.so 3137595842946788) struct sizes + `ELF-dynsym
confirmed` disasm of CGSceneData/CGIrradianceVolumesData/CGraphT/SNodeGraph/CSimpleKeyTable/
CTable<CSimpleKey>/SShapeListT/SSplineData/CConvex/SGSoundVolumeData/SGSoundPortalData/
SGSoundPortalGroupData::Serialize; `stream-confirmed` + `export-validated` byte-exact
round-trip on 626/626. Map-authoring s38/s39/s43/s44/s45. Finding: findings/scene-v3-serialize-graph.md.
"""
import struct

STRUCTURED = True

# On-disk record strides (POD; confirmed by corpus consume-to-EOF + disasm member graph)
C3VECTOR = 12
U32 = 4
CSYMBOL64 = 8
CVERTEX = 16                # CVertex {targethead,sourcehead,id,flags} u32×4 (DWARF size 16)
CEDGE = 8                   # CEdge {vhandle u32, nextedge u32} (DWARF size 8; was wrongly 16 — edges empty in per-prop scenes so never exercised)
CPAIR_U32 = 8
CSIMPLEKEY_U64_SYM = 16     # CSimpleKey<u64,CSymbol64> {u64 key, u64 value}
CSIMPLEKEY_U32_SYM = 16     # CSimpleKey<u32,CSymbol64> {u64 hash, u64 index}
CSIMPLEKEY_U32_U32 = 8      # CSimpleKey<u32,u32>
SGMESHSHADERSET = 24
SSET = 16                   # {CSymbol64 name, u32 group, u32 flags} (bulk-copied)
SGROUP = 48                 # CTable<SGroup> bulk-copies the 48B runtime struct
CTRANSFQ = 32               # quaternion(4f) + translation(3f) + pad/scale(1f)
CSPHERE = 16
COBB = 40                   # {center3, extent3, quat4} = 10 f32
CPLANE = 16                 # CTable<CPlane> element {C3Vector normal, float d} (convex faces)
# s44: SSplineData / CConvex shape-list (member-10 idx 4/5) elements are NOT flat strides —
# they are structs-OF-CTables serialized per-element (CTableXT<T>::Serialize -> SerializeArray
# -> T::Serialize). Decoded in _spline_elem_read / _convex_elem_read below (no longer 0/RAISE).
SGINSTANCEDATA = 168        # particleeffects record (bulk-copied POD)
# Lead-member record strides — DWARF byte_size of the runtime struct (CTable bulk-copies
# the runtime struct, per the SGroup-48 rule), export-validated by corpus consume-to-EOF.
# Sizes from libr15.so build 3137595842946788 DWARF (s39). DWARF-confirmed + stream-confirmed.
SGLIGHTPARAMS = 360         # member 1 lights        (CTable<SGLightParams>)
SGVOLLIGHTPARAMS = 296      # member 2 vlights       (CTable<SGVolumetricLightParams>)
SGATMOVOLPARAMS = 120       # member 3 avolumes      (CTable<SGAtmosphericVolumeParams>)
SGIRRADVOLDATA = 120        # member 6 ivdata.volumes(CTable<SGIrradianceVolumeData>)
SGACTORDATA = 56            # member 8 actors        (CTable<SGActorData>)
# s45: the member-12/13/14 sound lists are NOT flat PODs — they are structs-OF-CTables
# (per-element Serialize, disasm-confirmed). The s39 DWARF byte_size (304/128/56) is the
# RUNTIME struct size, not the on-disk stride. Decoded in _soundvol_elem_read /
# _portalgroup_elem_read + the stride-128 member-13 ctable below.
#   member 12 soundvolumelist  = CTableXT<SGSoundVolumeData>  (variable element, see helper)
#   member 13 soundportallist  = CTable<SGSoundPortalData>    (FLAT stride 128, see below)
#   member 14 soundportalgrouplist = CTableXT<SGSoundPortalGroupData> (element = CTable<u64>)
# Sub-record strides inside SGSoundVolumeData (disasm SGSoundVolumeData::Serialize @0x227c880):
SREVERBBUSINFO = 8          # reverb   CTable<SReverbBusInfo>  {CSoundSymbol(4), real(4)}
SSOUNDSWITCHDATA = 8        # switches CTable<SSoundSwitchData>{2x CSoundSymbol(4)}
SGSOUNDVOLCONN = 16         # connectiondata CTable<SGSoundVolumeConnectionData> {CSymbol64(8), symbol(8)}
CSOUNDSYMBOL = 4            # ambient inner CTable<CSoundSymbol>
U64 = 8                     # portalgroup inner CTable<u64>
# member 13 SGSoundPortalData is FLAT: name(8)+COBB(40)+8*CSymbol64(64)+symbol(8)+float(4)
# = 124 runtime bytes + a 4-byte per-element trailer (=0) = 128 on-disk stride. The trailer is
# written by the container's per-element loop (SerializeArray, GOT-resolved — same +4 that
# CConvex carries below); empirically pinned + export-validated by byte-exact round-trip.
SGSOUNDPORTALDATA = 128     # member 13 soundportallist stride (124 fields + 4 trailer)
SGAOVOLUME = 112            # SGAOVolumeData (bulk-copied POD)
CBOXTREE_SNODE = 32         # CBoxTree::SNode {aabb 6*f32, u32, u32}

# version-head prefix: N fixed 360B instance-header blocks (version+self-hash+world
# matrix+bounds) before the member graph. N = first block's word[0]; each block carries
# the shared landmark constants at word[3]/word[4] (see VHEAD_SIG). s38 2nd pass.
VHEAD_BLOCK = 360
VHEAD_SIG = (0x0127f3c0, 0x314bca1c)   # u32 @ +0x0c, +0x10 — present in every block


def _vhead_len(buf):
    """Length of the version-head prefix, or 0 if this is a plain zero-head scene.
    RAISE-safe: only fires on the exact landmark; a wrong body still fails the EOF check."""
    if len(buf) < 20:
        return 0
    n, sig0, sig1 = struct.unpack_from("<I", buf, 0)[0], \
        struct.unpack_from("<I", buf, 12)[0], struct.unpack_from("<I", buf, 16)[0]
    if n == 0 or (sig0, sig1) != VHEAD_SIG:
        return 0
    plen = n * VHEAD_BLOCK
    if plen >= len(buf):
        raise AssertionError(f"vhead {n}*{VHEAD_BLOCK} >= len {len(buf)}")
    return plen


class _R:
    """Forward byte cursor; every consume is count/size-driven and records are
    carried verbatim. RAISES on any structural surprise (the gate)."""
    __slots__ = ("b", "o")

    def __init__(self, b):
        self.b = b
        self.o = 0

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def take(self, n):
        if n < 0 or self.o + n > len(self.b):
            raise AssertionError(f"take {n} past end @0x{self.o:x}/{len(self.b)}")
        s = self.b[self.o:self.o + n]
        self.o += n
        return s

    def ctable(self, stride):
        """[u32 count][count*stride] -> (count, raw_records)."""
        c = self.u32()
        if c and stride == 0:
            raise AssertionError(f"non-empty table count={c} but stride unknown @0x{self.o:x}")
        return c, self.take(c * stride)

    def keytable(self, kstride):
        """CSimpleKeyTable<K,_,V> on disk = CTable<CSimpleKey<K,V>> (the combined
        key+value records) followed by a single u32 CFlagsT — NOT two parallel CTables.

        Disasm (libr15.so 3137595842946788): CSimpleKeyTable<...>::Serialize @0x3adecfc
        calls the inner CTable<CSimpleKey<K,V>>::Serialize (this+0) then
        Serialize<u32,1>(CFlagsT<u32>&) (this+0x38). The inner CTable::Serialize
        @0x3adee58 writes [u32 count] (vtable+0x90) + count*stride bulk records
        (vtable+0x88); everything after is the in-memory hash-bucket rebuild
        (CTable::GrowBy — NO stream bytes), so the bucket index is NOT on disk.
        CSimpleKey<u64,CSymbol64> stride = 16 (probe-confirmed across 9 whole-map
        scenes), CSimpleKey<u32,u32> = 8, CSimpleKey<u32,CSymbol64> = 16.

        The s38 "two parallel CTables (keys + values)" model was byte-exact only for
        the EMPTY case: [u32 0 count][u32 0 flags] = the same 8 bytes. Non-empty maps
        broke it (the second u32 is the flags, never a values count)."""
        c = self.u32()
        if c and kstride == 0:
            raise AssertionError(f"non-empty keytable count={c} but stride unknown @0x{self.o:x}")
        rec = self.take(c * kstride)
        flags = self.u32()
        return (c, rec, flags)

    def memblock(self):
        """non-bulk CMemBlock [u32 size][size bytes]."""
        sz = self.u32()
        return sz, self.take(sz)

    def memblock_bulk(self):
        """bulk CMemBlock [u32 size][u64 attach][size bytes] (ivdata sample blobs)."""
        sz = self.u32()
        attach = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        return sz, attach, self.take(sz)


# member-10 shapelists = 6 SShapeListT<T> in this order; idx 0-3 carry a flat-POD CTableXT<T>
# (stride below), idx 4/5 a per-element CTableXT<T> (struct-OF-CTables). s44.
SHAPELIST_KINDS = ("c3vector", "ctransfq", "csphere", "cobb", "spline", "convex")
_SHAPELIST_POD_STRIDE = {"c3vector": C3VECTOR, "ctransfq": CTRANSFQ,
                         "csphere": CSPHERE, "cobb": COBB}


def _spline_elem_read(r):
    """One SSplineData (SSplineData::Serialize @0x3c1d60c, libr15 3137595842946788):
    u32 + CTable<float>(knots) + CTable<C3Vector>(controlpoints) + u32 +
    CTable<C3Vector>(samples) + CTable<C3Vector>(derivatives). The 2 u32 are the
    +0x40 inline scalars (one leading, one between controlpoints and samples)."""
    s0 = r.u32()
    knots = r.ctable(U32)            # CTable<float>, stride 4
    controlpoints = r.ctable(C3VECTOR)
    s1 = r.u32()
    samples = r.ctable(C3VECTOR)
    derivatives = r.ctable(C3VECTOR)
    return (s0, knots, controlpoints, s1, samples, derivatives)


def _convex_elem_read(r):
    """One CConvex: CTable<CPlane>(faces, stride 16) + 2 C3Vector + a 4-byte per-element
    trailer (=0). CConvex::Serialize @0x185ae18 itself writes only faces + 2 C3Vector; the
    +4 is written by the container's per-element loop (SerializeArray<CConvex>, GOT-resolved).
    s44 left this trailer off because member-10 convex shape lists are EMPTY corpus-wide; s45
    STREAM-VALIDATED it via the soundvol `volumes` path (combustion has a 9-convex element;
    the +4 sits between adjacent convexes). Captured verbatim so a non-zero trailer round-trips."""
    planes = r.ctable(CPLANE)
    c0 = r.take(C3VECTOR)
    c1 = r.take(C3VECTOR)
    trailer = r.take(4)
    return (planes, c0, c1, trailer)


def _ambient_elem_read(r):
    """One SAmbientInfo (CTableXT<SAmbientInfo> element): CSoundSymbol(4) + real(4) +
    CTable<CSoundSymbol>. No per-element trailer (unlike CConvex; air_brake stream-confirmed)."""
    head = r.take(8)                 # CSoundSymbol + real
    inner = r.ctable(CSOUNDSYMBOL)
    return (head, inner)


def _soundvol_elem_read(r):
    """One SGSoundVolumeData (SGSoundVolumeData::Serialize @0x227c880): 9 fields in
    declaration order — name/nodeid (CSymbol64), reverb (CTable st8), priority+portalprog
    (8 inline bytes), ambient (CTableXT<SAmbientInfo>), switches (CTable st8), volumes
    (CTableXT<CConvex>, each convex carrying its +4 trailer), connectiondata (CTable st16).
    The element itself has NO trailer (fission: the next element's nodeid follows the last
    connectiondata record immediately)."""
    head = r.take(16)                # name + nodeid
    reverb = r.ctable(SREVERBBUSINFO)
    scalars = r.take(8)              # priority(int32) + portalprogression(float)
    amb_c = r.u32()
    ambient = [_ambient_elem_read(r) for _ in range(amb_c)]
    switches = r.ctable(SSOUNDSWITCHDATA)
    vol_c = r.u32()
    volumes = [_convex_elem_read(r) for _ in range(vol_c)]
    conn = r.ctable(SGSOUNDVOLCONN)
    return dict(head=head, reverb=reverb, scalars=scalars, ambient=ambient,
                switches=switches, volumes=volumes, conn=conn)


def _portalgroup_elem_read(r):
    """One SGSoundPortalGroupData (SGSoundPortalGroupData::Serialize @0x227d888): a single
    CTable<u64> (the member portal indices). No per-element trailer (combustion: 3 groups
    [0,1]/[2,3]/[4,5,6] index its 7 portals with materials immediately after)."""
    return r.ctable(U64)


def _shapelist(r, kind):
    """SShapeListT<T> = CSimpleKeyTable + CSimpleKeyTable + CTableXT<T> (the data table).
    For spline/convex the data table is a per-element CTableXT (no bulk trailer — the
    CSerializerEnableBulk dtor writes 0 bytes; probe-confirmed to EOF)."""
    kt0 = r.keytable(CSIMPLEKEY_U64_SYM)
    kt1 = r.keytable(CSIMPLEKEY_U64_SYM)
    if kind == "spline":
        c = r.u32()
        data = (c, [_spline_elem_read(r) for _ in range(c)])
    elif kind == "convex":
        c = r.u32()
        data = (c, [_convex_elem_read(r) for _ in range(c)])
    else:
        data = r.ctable(_SHAPELIST_POD_STRIDE[kind])
    return (kind, kt0, kt1, data)


def _nodegraph(r):
    """SNodeGraph = CGraphT + roots + lookup + types + names."""
    # CGraphT
    verts = r.ctable(CVERTEX)
    sedges = r.ctable(CEDGE)
    tedges = r.ctable(CEDGE)
    srcleafs = r.keytable(CSIMPLEKEY_U32_U32)
    tgtleafs = r.keytable(CSIMPLEKEY_U32_U32)
    idxremap = r.ctable(U32)
    deadlist = r.ctable(CPAIR_U32)
    inline4 = struct.unpack_from("<4I", r.b, r.o)
    r.o += 16
    # SNodeGraph tail
    roots = r.ctable(U32)
    lookup = r.keytable(CSIMPLEKEY_U64_SYM)
    types = r.ctable(U32)
    names = r.ctable(CSYMBOL64)
    return dict(verts=verts, sedges=sedges, tedges=tedges, srcleafs=srcleafs,
                tgtleafs=tgtleafs, idxremap=idxremap, deadlist=deadlist,
                inline4=inline4, roots=roots, lookup=lookup, types=types, names=names)


def read(buf):
    r = _R(buf)

    # --- optional version-head prefix (N*360B instance headers) carried verbatim ---
    vlen = _vhead_len(buf)
    vhead = r.take(vlen)

    # --- members 1-5 (lights..dirlightindices) ---
    lead = []
    for stride in (SGLIGHTPARAMS, SGVOLLIGHTPARAMS, SGATMOVOLPARAMS, C3VECTOR, U32):
        lead.append(r.ctable(stride))

    # --- member 6 ivdata (CGIrradianceVolumesData, bulk-wrapped) ---
    # disasm-confirmed (CGIrradianceVolumesData::Serialize @0x3acb124): 5 CTables
    # (volumes + 4 CSymbol64 name tables) + 4 plain CMemBlocks (samples / dirshadow /
    # lightshadow / diffuse, each [u32 size][bytes]) + an 8B bulk-scope close marker
    # (CSerializerEnableBulk dtor; =0, captured verbatim). `attached` is runtime-only
    # (not serialized — the fn returns after diffusesamples). The empty form is 44B = the
    # s38 "2 bulk blobs w/ u64 attach" model only because all those bytes are zero.
    iv_tables = [r.ctable(SGIRRADVOLDATA if i == 0 else CSYMBOL64) for i in range(5)]  # volumes + 4 name tables
    iv_blobs = [r.memblock() for _ in range(4)]   # samples/dirshadow/lightshadow/diffuse
    iv_bulk_trailer = r.take(8)                   # bulk-scope close marker (=0)

    # --- member 7 particleeffects, scalars, member 8 actors ---
    particleeffects = r.ctable(SGINSTANCEDATA)
    bonecount = r.u32()
    boundingboxidx = r.u32()
    actors = r.ctable(SGACTORDATA)       # member 8 (CTable<SGActorData>)

    # --- member 9 shapelist : the per-scene attach-heap CMemBlock (verbatim) ---
    shapelist = r.memblock()

    # --- member 10 shapelists : 6 SShapeListT<T> ---
    shapelists = [_shapelist(r, kind) for kind in SHAPELIST_KINDS]

    # --- member 11 nodegraph ---
    nodegraph = _nodegraph(r)

    # --- members 12-14 sound lists (s45 decode) ---
    sv_c = r.u32()                                   # member 12 CTableXT<SGSoundVolumeData>
    soundvol = (sv_c, [_soundvol_elem_read(r) for _ in range(sv_c)])
    soundportal = r.ctable(SGSOUNDPORTALDATA)        # member 13 CTable<SGSoundPortalData> st128
    spg_c = r.u32()                                  # member 14 CTableXT<SGSoundPortalGroupData>
    soundportalgroup = (spg_c, [_portalgroup_elem_read(r) for _ in range(spg_c)])

    # --- tail: materials, shadersets, flags, pad, bbox ---
    materials = r.ctable(CSYMBOL64)
    shadersets = r.ctable(SGMESHSHADERSET)
    flags = r.u32()
    padding = r.u32()
    bbox = r.take(24)

    # --- member 18 sets (CGSceneSetsData) ---
    sets_set = r.ctable(SSET)
    sets_setnames = r.keytable(CSIMPLEKEY_U32_SYM)
    sets_groups = r.ctable(SGROUP)
    sets_groupnames = r.keytable(CSIMPLEKEY_U32_SYM)

    # --- members 19-22 ---
    aovolumes = r.ctable(SGAOVOLUME)
    jointoffsets = r.ctable(CTRANSFQ)
    boxtree = r.ctable(CBOXTREE_SNODE)   # bulk CTable<SNode>; bulk adds no on-disk size field
    debugdata = [r.ctable(CSYMBOL64) for _ in range(3)]

    if r.o != len(buf):
        raise AssertionError(f"did not consume to EOF: 0x{r.o:x} != 0x{len(buf):x}")

    return dict(vhead=vhead, lead=lead, iv_tables=iv_tables, iv_blobs=iv_blobs,
                iv_bulk_trailer=iv_bulk_trailer,
                particleeffects=particleeffects, bonecount=bonecount,
                boundingboxidx=boundingboxidx, actors=actors, shapelist=shapelist,
                shapelists=shapelists, nodegraph=nodegraph, soundvol=soundvol,
                soundportal=soundportal, soundportalgroup=soundportalgroup,
                materials=materials, shadersets=shadersets, flags=flags,
                padding=padding, bbox=bbox, sets_set=sets_set,
                sets_setnames=sets_setnames, sets_groups=sets_groups,
                sets_groupnames=sets_groupnames, aovolumes=aovolumes,
                jointoffsets=jointoffsets, boxtree=boxtree, debugdata=debugdata)


def _wtable(out, t):
    out += struct.pack("<I", t[0])
    out += t[1]


def _wkeytable(out, kt):
    # (count, records, flags) -> [u32 count][records][u32 flags]
    out += struct.pack("<I", kt[0]); out += kt[1]
    out += struct.pack("<I", kt[2])


def _wspline_elem(out, e):
    s0, knots, controlpoints, s1, samples, derivatives = e
    out += struct.pack("<I", s0)
    _wtable(out, knots); _wtable(out, controlpoints)
    out += struct.pack("<I", s1)
    _wtable(out, samples); _wtable(out, derivatives)


def _wconvex_elem(out, e):
    planes, c0, c1, trailer = e
    _wtable(out, planes); out += c0; out += c1; out += trailer


def _wambient_elem(out, e):
    head, inner = e
    out += head; _wtable(out, inner)


def _wsoundvol_elem(out, e):
    out += e["head"]
    _wtable(out, e["reverb"])
    out += e["scalars"]
    out += struct.pack("<I", len(e["ambient"]))
    for a in e["ambient"]:
        _wambient_elem(out, a)
    _wtable(out, e["switches"])
    out += struct.pack("<I", len(e["volumes"]))
    for cv in e["volumes"]:
        _wconvex_elem(out, cv)
    _wtable(out, e["conn"])


def _wsoundvol(out, sv):
    count, elems = sv
    out += struct.pack("<I", count)
    for e in elems:
        _wsoundvol_elem(out, e)


def _wportalgroup(out, spg):
    count, groups = spg
    out += struct.pack("<I", count)
    for g in groups:
        _wtable(out, g)


def _wshapelist(out, sl):
    kind, kt0, kt1, data = sl
    _wkeytable(out, kt0); _wkeytable(out, kt1)
    if kind == "spline":
        out += struct.pack("<I", data[0])
        for e in data[1]:
            _wspline_elem(out, e)
    elif kind == "convex":
        out += struct.pack("<I", data[0])
        for e in data[1]:
            _wconvex_elem(out, e)
    else:
        _wtable(out, data)


def write(obj):
    out = bytearray()
    out += obj["vhead"]
    for t in obj["lead"]:
        _wtable(out, t)
    for t in obj["iv_tables"]:
        _wtable(out, t)
    for sz, blob in obj["iv_blobs"]:
        out += struct.pack("<I", sz); out += blob
    out += obj["iv_bulk_trailer"]
    _wtable(out, obj["particleeffects"])
    out += struct.pack("<II", obj["bonecount"], obj["boundingboxidx"])
    _wtable(out, obj["actors"])
    out += struct.pack("<I", obj["shapelist"][0]); out += obj["shapelist"][1]
    for sl in obj["shapelists"]:
        _wshapelist(out, sl)
    ng = obj["nodegraph"]
    for k in ("verts", "sedges", "tedges"):
        _wtable(out, ng[k])
    _wkeytable(out, ng["srcleafs"]); _wkeytable(out, ng["tgtleafs"])
    _wtable(out, ng["idxremap"]); _wtable(out, ng["deadlist"])
    out += struct.pack("<4I", *ng["inline4"])
    _wtable(out, ng["roots"]); _wkeytable(out, ng["lookup"])
    _wtable(out, ng["types"]); _wtable(out, ng["names"])
    _wsoundvol(out, obj["soundvol"])
    _wtable(out, obj["soundportal"])
    _wportalgroup(out, obj["soundportalgroup"])
    _wtable(out, obj["materials"]); _wtable(out, obj["shadersets"])
    out += struct.pack("<II", obj["flags"], obj["padding"])
    out += obj["bbox"]
    _wtable(out, obj["sets_set"]); _wkeytable(out, obj["sets_setnames"])
    _wtable(out, obj["sets_groups"]); _wkeytable(out, obj["sets_groupnames"])
    _wtable(out, obj["aovolumes"]); _wtable(out, obj["jointoffsets"])
    _wtable(out, obj["boxtree"])
    for t in obj["debugdata"]:
        _wtable(out, t)
    return bytes(out)


def roundtrip(buf):
    return write(read(buf))
