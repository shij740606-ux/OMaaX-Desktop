# 植物顺式作用元件分类规则 (PlantCARE-based)

## 分类体系

### 1. 核心启动子元件 (Core Promoter Elements)
- **TATA-box**: TATA-box, AT~TATA-box, TATA
- **CAAT-box**: CAAT-box, CCAAT-box
- **Inr (Initiator)**: Inr, Y-patch

### 2. 光响应元件 (Light Responsive Elements)
- **G-box/G-Box**: G-box, G-Box, G-box-like
- **GT1-motif**: GT1-motif, GT-1
- **I-box**: I-box, I-box core
- **Sp1**: Sp1 (light responsive)
- **Box 4**: Box 4
- **GATA-motif**: GATA-motif

### 3. 激素响应元件 (Hormone Responsive Elements)

#### 3.1 ABA响应
- **ABRE**: ABRE, ABA-responsive element

#### 3.2 赤霉素响应
- **P-box**: P-box, GARE (gibberellin-responsive element)
- **Py-box**: Py-box

#### 3.3 茉莉酸响应
- **CGTCA-motif**: CGTCA-motif
- **TGACG-motif**: TGACG-motif

#### 3.4 水杨酸响应
- **TCA-element**: TCA-element
- **as-1**: as-1

#### 3.5 生长素响应
- **TGA-element**: TGA-element
- **AuxRE**: AuxRE

#### 3.6 乙烯响应
- **ERE**: ERE (ethylene-responsive element)

### 4. 胁迫响应元件 (Stress Responsive Elements)

#### 4.1 干旱/脱水
- **DRE/CRT**: DRE, CRT (C-repeat)
- **MBS**: MBS (MYB binding site, drought)

#### 4.2 低温
- **LTR**: LTR (low temperature responsive)

#### 4.3 厌氧
- **ARE**: ARE (anaerobic responsive element)

#### 4.4 氧化胁迫
- **STRE**: STRE (stress responsive element)

#### 4.5 伤口/病原体
- **W box**: W box, W-box
- **WRE**: WRE3, WRE

### 5. 转录因子结合位点 (TF Binding Sites)

#### 5.1 MYB家族
- **MYB**: MYB, Myb, MYB-like sequence, MYB recognition site
- **MBS**: MBS (MYB binding site)
- **MRE**: MRE (MYB recognition element)

#### 5.2 MYC/bHLH家族
- **MYC**: MYC, MYC recognition site
- **E-box**: E-box, CANNTG

#### 5.3 WRKY家族
- **W box**: W box (也是WRKY结合位点)

#### 5.4 bZIP家族
- **ABRE**: ABRE (也是bZIP结合位点)
- **G-box**: G-box (也是bZIP结合位点)

### 6. 发育调控元件 (Developmental Elements)

#### 6.1 种子特异性
- **RY-element**: RY-element, CATGCATG
- **AAGAA-motif**: AAGAA-motif

#### 6.2 分生组织
- **CAT-box**: CAT-box

#### 6.3 胚乳
- **GCN4-motif**: GCN4-motif
- **Skn-1 motif**: Skn-1 motif

### 7. 其他功能元件
- **TCT-motif**: TCT-motif
- **Unnamed**: Unnamed__1, Unnamed__4, Unnamed__6 等

## 分类优先级规则

当一个元件可能属于多个类别时，按以下优先级：
1. 核心启动子元件 (最高优先级)
2. 激素响应元件
3. 胁迫响应元件
4. 转录因子结合位点
5. 光响应元件
6. 发育调控元件
7. 其他 (最低优先级)

## 参考来源

1. PlantCARE Database: https://bioinformatics.psb.ugent.be/webtools/plantcare/
2. Lescot et al. (2002) PlantCARE, a database of plant cis-acting regulatory elements and a portal to tools for in silico analysis of promoter sequences. Nucleic Acids Res.
3. New PLACE Database: https://www.dna.affrc.go.jp/PLACE/
