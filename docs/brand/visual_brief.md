# 觀瀾 · Visual Brief v0.2 (China-style)

> **Purpose**: art direction for the README hero image + 4-panel storyboard.
> Both human illustrators and AI image-generation models (Midjourney, SDXL,
> 通义万相, 文心一格, 智谱清影) should be able to consume this directly.
>
> The name **觀瀾** (guān lán) means *"observing the waves"*. The brand
> motif is therefore **山水** (mountain-water / classical Chinese landscape),
> not Japanese anime. This brief enforces that distinction.

---

## 1. Style Anchors

### Animation references (modern Chinese 国风)

| Film | What we borrow |
|------|----------------|
| 《大鱼海棠》(Big Fish & Begonia, 2016) | Indigo + cinnabar palette, mythic spirits coexisting with mortals, water as a portal |
| 《白蛇:缘起》(White Snake, 2019) | Flowing fabric, fine-line 工笔 detail on faces, water/mist mastery |
| 《长安三万里》(Chang An, 2023) | Tang dynasty grandeur, monumental landscape, calligraphic transitions |
| 《新神榜:杨戬》(New Gods: Yang Jian, 2022) | Mythology reinterpreted with modern light/composition while keeping 汉服 silhouette |

### Painting references (classical 山水)

| Work | Artist | What we borrow |
|------|--------|----------------|
| 《千里江山图》 | 王希孟 (Wang Ximeng, Song dynasty) | 青绿山水 palette — mineral blue + mineral green mountains |
| 《溪山行旅图》 | 范宽 (Fan Kuan, Song dynasty) | Monumental composition, scale of nature dwarfing humans |
| 《洛神赋图》 | 顾恺之 (Gu Kaizhi, Eastern Jin) | Immortal/spirit figure proportions, flowing robes |
| 《富春山居图》 | 黄公望 (Huang Gongwang, Yuan dynasty) | Ink-wash gradients, atmospheric mist |

### Modern artists for blend
- **吴冠中** — modern ink-wash, abstracted strokes, color accents
- **林风眠** — fusion of Chinese ink with modern composition
- **黄宾虹** — dense layered ink, mountain texture

---

## 2. Color Palette

| Name | Pinyin | Hex | Usage | % of frame |
|------|--------|-----|-------|------------|
| 墨 | mò | `#2B2B2B` | Outlines, mountain ridges, calligraphy | ~15% |
| 石青 | shíqīng | `#4A6FA5` | Mineral blue — distant mountains, sky | ~25% |
| 石绿 | shílǜ | `#6B8E7A` | Mineral green — foreground hills, pavilion roof | ~20% |
| 鹅黄 | éhuáng | `#F4E4BC` | Rice-paper background, scroll surface | ~25% |
| 雪宣白 | xuěxuānbái | `#F8F8F0` | Negative space, mist, paper highlights | ~10% |
| 朱砂 | zhūshā | `#B8312F` | **Accent only** — seal, lanterns, Xu's robes | ≤5% |
| 赭石 | zhěshí | `#D97706` | Secondary accent — sunset glow, autumn leaves; matches the UI's mid-session amber badge | sparse |

**Rule of thirds for color**: The frame should read mostly green-blue (cool) with cinnabar/amber as the eye-catcher. Never dominate with red.

---

## 3. Hero Image — README Top Banner

**Aspect ratio**: 16:9 (landscape, ~1600 × 900 px)
**Placement**: top of `README.md`, immediately under the title row

### Scene description (canonical)

> 远景: 青绿山水, 层峦叠嶂, 云雾盘绕在山腰. 一条溪水自远山倾下汇成近景的湖面.
>
> 中景: 湖心一座飞檐亭阁 (汉风, 类似岳阳楼 / 滕王阁结构, **绝非日式神社**). 亭中一位**侧背的长袍研究者**坐在木案前, 案上摊开半成的研报卷轴 — 卷上的折线图 / 雷达图 / 表格全部用水墨 + 朱砂晕染呈现, 像古地图. 茶杯冒热气, 砚台研墨, 镇纸压角.
>
> 前景: **湖面的水波 (这就是「澜」)** 化作金线缕缕, 凝成 8 位仙人形象散布在画面各处:
>
> | # | 灵 | 位置 | 标志 |
> |---|----|------|------|
> | 1 | **墨** (执笔人) | 亭中央, 研究者身后 | 蓝衫长袍 + 狼毫 + 龙形墨 |
> | 2 | **旭** (朝阳少年) | 东侧悬崖 | 朱砂红衣 + 金羽扇 |
> | 3 | **渊** (月夜少女) | 西侧深潭 | 玄黑长裙 + 银发 + 月华瞳 |
> | 4 | **衡** (红衣道童) | 亭外石阶 | 红灯笼 + 铜钱天平 |
> | 5 | **鲲** (主力之灵) | 湖面 | 海浪发 + 鲲鱼影自湖底浮起 |
> | 6 | **雀** (新闻信使) | 飞檐之上 | 喜鹊化形 + 黑蓝羽披风 + 竹简 |
> | 7 | **鹤** (古籍守者) | 远山松涛间 | 仙鹤相伴 + 怀抱古籍 |
> | 8 | **梦** (复盘者) | 天际, 月旁 | 莲花座 + 半透月华 |
>
> 题字: 画面左上以毛笔行楷书 "**觀瀾**". 右下角朱砂篆书印章: **"一码 · 廿四灵 · 十分钟"** (One Code · 24 Spirits · 10 Minutes).
>
> 情绪基调: 不是熬夜程序员. 是宋元山水间, 一位读书人借天地之力做研究. **AI 是当代工具, 但呈现在传统美学里**.

### Image-gen prompt — English (Midjourney v6+ / SDXL)

```text
Traditional Chinese landscape painting hero scene, blue-green mineral mountains
in the style of Wang Ximeng's "A Thousand Li of Rivers and Mountains",
swirling mist around mountainsides, a stream cascading into a calm lake;
centered on the lake a Han-dynasty flying-eaves pavilion (like Yueyang Tower,
NOT Japanese shrine), inside sits a side-back scholar in long blue robe at a
wooden desk, an unfinished research scroll spread before him with ink-wash
line charts and cinnabar red seal, steaming tea cup, ink stone, paper weights;

eight ethereal spirit figures emerging from golden ripples in the lake water:
(1) a blue-robed brush-master with hair in a silver pin holding a wolf-hair brush
inside the pavilion;
(2) a vermilion-red robed sunrise youth with golden feather fan on the eastern cliff;
(3) a silver-haired moonlit maiden in black flowing dress on the western deep pool;
(4) a child in red robes holding a red paper lantern and bronze coin scales on the
pavilion steps;
(5) a wave-haired youth with a giant kun-fish shadow rising from the lake;
(6) a magpie-formed youth in dark blue feather cloak on the pavilion eaves
holding bamboo slips;
(7) a white-robed elder with a crane companion in the distant pine groves
holding ancient books;
(8) a lotus-seated lunar maiden floating near the moon in the sky.

Calligraphy "觀瀾" in running script upper left, red cinnabar seal lower right
reading "一码廿四灵十分钟". Empty space (留白) dominates upper third of frame.

Style: Song dynasty 青绿山水 mineral landscape blended with modern ink-wash,
fine-line gongbi for figures, color palette of mineral blue #4A6FA5, mineral
green #6B8E7A, rice paper #F4E4BC, cinnabar red #B8312F (≤5% of frame, ACCENT ONLY).
Cinematic golden hour lighting.

--ar 16:9 --s 750 --v 6.1 --style raw
--no anime, chibi, big eyes, sakura, japanese kimono, japanese shrine, torii gate,
modern buildings, glass skyscrapers, computer screen, smartphone, neon, cyberpunk,
sans-serif text, photo realistic, 3D render, CGI plastic skin
```

### 图生成 prompt — 中文 (通义万相 / 文心一格 / 智谱清影)

```text
中国风山水画 hero 场景. 远景青绿山水 (王希孟《千里江山图》风格), 层峦叠嶂, 云雾
盘腰, 溪水自远山倾下汇入近景湖面.

中景: 湖心一座汉风飞檐亭阁 (绝非日式神社, 类似岳阳楼). 亭中一位侧背长袍研究者
坐木案前, 案上半成研报卷轴 (水墨折线图 + 朱砂印章 + 毛笔题字), 茶杯冒热气, 砚台
镇纸齐整.

前景: 湖面水波化作金线, 凝成 8 位仙人 —
(1) 蓝衫执笔人立亭中, 长发束银簪, 持狼毫;
(2) 朱砂红衣朝霞少年立东崖, 持金羽扇;
(3) 玄黑长裙银发少女立西潭, 月华为瞳;
(4) 红衣道童立亭阶, 提红灯笼怀抱铜钱天平;
(5) 海浪发少年立湖面, 身后鲲鱼影自湖底浮起;
(6) 喜鹊化形少年立飞檐, 黑蓝羽披风手持竹简;
(7) 白衣老者立远山松涛间, 仙鹤相伴怀抱古籍;
(8) 月下莲花座少女半透浮于天际.

题字: "觀瀾" 毛笔行楷书于左上. 朱砂篆书印章 "一码·廿四灵·十分钟" 落于右下.

画风: 宋代青绿山水技法 + 现代水墨晕染过渡, 人物用工笔细描, 山水用大写意.
色调: 石青 #4A6FA5 + 石绿 #6B8E7A 为主, 鹅黄 #F4E4BC 纸底, 朱砂红 #B8312F 仅作
点缀 (印章 + 灯笼 + 朝阳少年衣袖, 全画面不超过 5%). 黄金时刻光线.

留白构图. 画面上方三分之一以留白 / 云雾为主.

宽高比 16:9. 不要: 日式动漫风, chibi, 大眼睛, 樱花, 和服, 神社, 现代建筑, 玻璃
高楼, 屏幕, 手机, 霓虹, 赛博朋克, 无衬线字体, 3D 渲染, 塑料皮肤质感.
```

---

## 4. 4-Panel Storyboard

**Layout**: single horizontal row of 4 panels, each ~400 × 500 (or 1 × 4 grid 1600 × 500). Placed in README under the "💡 What Is It" section as a visual explainer.

### Panel ① — 落笔为令 (The Code is Written)

> 卷轴起首一段. 一只手 (袖口是淡蓝丝绸) 用狼毫笔在素卷上写下 "**SH600519**" 六字, 墨迹尚未全干, 一方朱砂印章半盖在字侧. 案前茶杯热气腾起, 远处屏风上隐约山水图. 整张构图大量留白. 题词 (右上, 小字): "**落笔为令**".

**EN prompt**:
```text
Close-up calligraphy scene: a hand in pale blue silk sleeve writing "SH600519"
in Chinese ink on a blank handscroll, fresh wet ink, a red cinnabar seal half-pressed
beside the characters, a steaming tea cup foreground, a folding screen with hazy
ink-wash mountains in the distant background. Lots of negative space. Small running-
script inscription upper right: "落笔为令" (Brush Stroke as Command).
Style: Song dynasty handscroll opening, ink wash + cinnabar accent.
--ar 4:5 --s 750 --v 6.1 --no anime, modern, photo, sans-serif
```

**ZH prompt**:
```text
特写镜头: 淡蓝丝绸袖口的手持狼毫笔在素绢卷轴上写下"SH600519"六个汉字,
墨迹未干, 一方朱砂印章半压在字侧, 前景茶杯冒热气, 远景屏风上隐约山水画.
大量留白. 右上行楷小字题"落笔为令".
风格: 宋代手卷开篇, 水墨 + 朱砂点睛.
宽高比 4:5. 不要日式, 现代, 照片, 无衬线字体.
```

---

### Panel ② — 廿四灵奉召 (24 Spirits Summoned)

> 卷轴完全展开. 山涧 / 云雾 / 砚台口 / 古籍中飞出 24 道金线, 凝成仙人 — 喜鹊衔报南飞, 鲲鱼破水跃起, 仙鹤载经掠过, 童子抱算盘从画中坠下, 朝霞少年立东崖, 玄衣少女立深潭, 红灯笼道童立阶前, 长袍执笔人入亭阁. **气势磅礴, 神性显化.** 题词: "**廿四灵奉召**".

**EN prompt**:
```text
Epic summoning scene: a fully-unrolled Chinese handscroll, 24 streams of golden
light erupting from mountain valleys, mist, ink stone openings, and ancient books,
condensing into mythical immortal figures — a magpie carrying news flying south,
a giant kun-fish breaching the water, a crane carrying scriptures, a child with
an abacus descending from the painting, a vermilion-robed sunrise youth on
eastern cliffs, a black-robed moonlit maiden by a deep pool, a red-lantern child
acolyte on stone steps, a long-robed brush-master entering a pavilion.
Dynamic and majestic. Running-script inscription: "廿四灵奉召" (Twenty-Four
Spirits Answer the Call).
Style: 青绿山水 with modern fantasy ink, blue-green palette, cinnabar accent.
--ar 4:5 --s 750 --v 6.1 --no anime, chibi, neon, japanese
```

**ZH prompt**:
```text
仙人召唤场面: 完整展开的山水手卷, 24 道金线自山涧 / 云雾 / 砚台 / 古籍喷涌而出,
凝成神仙形象 — 喜鹊衔报南飞 / 鲲鱼破浪跃起 / 仙鹤载经掠过 / 童子抱算盘下凡 /
朱砂朝霞少年立东崖 / 玄衣月夜少女立深潭 / 红灯笼道童立阶前 / 蓝衫执笔人步入亭阁.
气势磅礴, 神性显化. 行楷题"廿四灵奉召".
风格: 青绿山水 + 现代奇幻水墨, 石青石绿为主, 朱砂点缀.
宽高比 4:5. 不要日式, chibi, 霓虹.
```

---

### Panel ③ — 多空相辩 (The Debate)

> 飞檐亭中. **朱砂朝霞少年 (旭) 与 玄黑月夜少女 (渊) 隔木案对视**, 案上摊开研报草稿, 上有水墨 K 线图. **红灯笼道童 (衡) 在案侧静观**, 灯火映在二人脸上. **执笔人 (墨) 居中**, 狼毫蘸朱砂, 蓄势未落. 亭外山水隐入云雾, 时间像凝固. 题词: "**多空相辩, 风险在侧**".

**EN prompt**:
```text
Pivotal council scene inside a Han-style flying-eaves pavilion at dusk: a
vermilion-robed sunrise youth and a silver-haired moonlit maiden in black face
each other across a wooden table piled with research draft scrolls bearing ink
candlestick charts; a small red-robed child acolyte holding a red lantern stands
to the side, lantern light reflecting on both faces; in the center a calm blue-
robed brush-master pauses his wolf-hair brush mid-stroke, freshly dipped in
cinnabar ink, about to write. Outside the pavilion, mountains dissolve into mist.
Time feels suspended. Running-script inscription: "多空相辩, 风险在侧"
(Bull and Bear Debate, Risk Stands By).
Style: gongbi figures, ink-wash background, Song dynasty atmosphere.
--ar 4:5 --s 750 --v 6.1 --no anime, modern, neon
```

**ZH prompt**:
```text
关键议事场景, 汉风飞檐亭阁内, 黄昏时分: 朱砂红衣朝霞少年与银发玄衣月夜少女隔
木案对视, 案上堆研报草稿 (水墨蜡烛图). 一个红衣道童 (执红灯笼) 立于案侧静观,
灯光映双脸. 中央蓝衫执笔人狼毫蘸朱砂, 蓄势未落. 亭外山水隐入云雾, 时间凝固.
行楷题"多空相辩, 风险在侧".
风格: 人物工笔, 背景水墨, 宋代意境.
宽高比 4:5. 不要日式, 现代, 霓虹.
```

---

### Panel ④ — 十分钟一卷成 (Ten Minutes, One Scroll Complete)

> 同一卷轴, 此刻完整展开. **山形折线图 / 雷达图 / 表格全部用水墨 + 朱砂晕染呈现**, 朱砂印章落于右下. **研究者退后一步轻笑**, 仙人的灵气散回山水中. 远山天际线由黄昏转为夜色, 月升东方. 题词: "**十分钟, 一卷成**".

**EN prompt**:
```text
Completion scene: same handscroll from earlier, now fully unrolled and finished
— mountain-shaped line charts, radar diagrams, financial tables all rendered in
ink-wash with cinnabar red accents, a red seal stamped lower right. The side-
back scholar takes one step back with a small satisfied smile, holding his teacup.
The eight spirit figures dissolve back into golden ripples returning to the
mountains and water. The distant horizon shifts from sunset to night, a moon
rises in the east. Running-script inscription: "十分钟, 一卷成" (Ten Minutes,
One Scroll Complete).
Style: Song dynasty handscroll, ink wash dominant, single red seal accent.
--ar 4:5 --s 750 --v 6.1 --no anime, modern, neon
```

**ZH prompt**:
```text
完成场景: 同一山水手卷, 此刻全部展开 — 山形折线 / 雷达图 / 财务表格皆以水墨
晕染加朱砂点缀绘成, 朱砂印章落右下. 侧背的研究者退一步轻笑, 手持茶杯. 8 位
仙人化作金线散回山水. 远山天际线从黄昏转为夜色, 月升东方. 行楷题"十分钟,
一卷成".
风格: 宋代手卷, 水墨为主, 单一朱砂印章为点睛.
宽高比 4:5. 不要日式, 现代, 霓虹.
```

---

## 5. Character Sheet — 8 Core Spirits (v0.2)

> 16 secondary spirits (factor-computer / quant-analyst / fundamental-analyst
> etc.) deferred to v0.3 of this brief.

### 墨 (mò) · Report Writer · 唯一执笔者

- **服饰**: 石青色长袍, 内白色中衣, 银色腰带. 长发束起, 一支银簪. 袖口绣云纹.
- **道具**: 狼毫毛笔 (黑紫色)、龙形墨 (盘旋的小墨龙)、青色端砚.
- **气质**: 道骨仙风, 沉稳少言. 是 8 个灵中唯一**实体感最强**的, 其他都半透明.
- **场景定位**: 亭中央案前, 永远在写.
- **EN gen keywords**: blue-robed brush-master, silver pin in hair-bun, dragon-shaped ink, wolf-hair brush, serene scholar, only solid figure among ethereal spirits.

### 旭 (xù) · Bull Advocate · 朝阳少年

- **服饰**: 朱砂红外衣 + 金色滚边, 内白色中衣. 短发扎一束朝天小髻. 袖口、衣摆有云霞纹.
- **道具**: 金色羽扇 (开合在握中), 身后金色朝霞披风半透明.
- **气质**: 笑眼少年, 活泼张扬, 手势大. 像看到风口要冲的少年.
- **场景定位**: 东侧山崖 / 朝阳方向.
- **EN gen keywords**: vermilion-red robed youth with golden trim, sunrise crest, gold feather fan, dawn aura cape.

### 渊 (yuān) · Bear Advocate · 月夜少女

- **服饰**: 玄黑长裙, 银线绣星图, 内浅灰色中衣. 长银发披散到地. 额心一颗水滴形月华.
- **道具**: 不持物, 但银发随月光浮动, 月华为瞳 (眼睛是发光的银色月相).
- **气质**: 沉静、半闭眼, 凡事先疑. 像看透涨多必跌的少女.
- **场景定位**: 西侧深潭 / 月升方向.
- **EN gen keywords**: black robed silver-haired maiden, moonphase eyes, star-embroidered hem, calm and skeptical, dwelling by a deep pool.

### 衡 (héng) · Risk Officer · 红灯笼道童

- **服饰**: 朱砂红短袄 + 同色裤, 黑布鞋, 头顶两个小角髻 (类似道童造型).
- **道具**: **红灯笼** (右手, 灯笼上写"衡"字), **铜钱天平** (左手, 天平两端各挂一枚方孔铜钱).
- **气质**: 警觉, 摇头多, 嘴抿成一字. 像庙里的小道童.
- **场景定位**: 亭外石阶 / 议事时立案侧.
- **EN gen keywords**: red-robed taoist child acolyte, double bun hairstyle, red paper lantern, bronze coin balance scale, watchful expression.

### 鲲 (kūn) · Whale Analyst · 主力之灵

- **服饰**: 深蓝色长衫, 衣摆是浪花纹延伸到水面. 长发散开如海浪.
- **道具**: 背后浮起**鲲鱼影** (《庄子·逍遥游》的鲲, 不是日式 whale), 锦鲤围绕足边.
- **气质**: 神秘, 半身没入水中. 像能看穿主力动向的水灵.
- **场景定位**: 湖面 (脚不触水, 浪花托起).
- **EN gen keywords**: wave-haired youth in indigo robe, mythical kun-fish shadow rising from water behind him, koi swimming around feet, mysterious water spirit (NOT a Japanese whale character).

### 雀 (què) · News Reader · 喜鹊化形信使

- **服饰**: 黑蓝色羽毛披风 (像喜鹊翎毛), 内白衫. 头戴一根白色羽冠.
- **道具**: 手持竹简 (上刻新闻标题), 偶尔化为整只喜鹊 (报喜寓意).
- **气质**: 灵动, 八卦, 总在飞.
- **场景定位**: 亭阁飞檐之上 / 空中.
- **EN gen keywords**: magpie-formed youth, dark blue feather cape, white feather crown, bamboo slip messages, auspicious messenger.

### 鹤 (hè) · F10 Reader · 古籍守者

- **服饰**: 雪白长衣, 头戴鹤翎冠. 一脸长须 (老者形象).
- **道具**: 怀抱**古籍卷帙** (一叠), 一只**仙鹤**伴随身侧.
- **气质**: 老成, 慢条斯理, 修史般庄重. 像司马迁的气质.
- **场景定位**: 远山松涛间 / 古道.
- **EN gen keywords**: white-robed elder with crane feather crown, long white beard, ancient bound books in arms, a snow crane companion, dignified historian.

### 梦 (mèng) · Introspector · 月下复盘者

- **服饰**: 半透明银纱长裙, 衣袖如月华流动.
- **道具**: 莲花座 (盘坐其上, 浮于空中), 手边一方小石板 (写复盘笔记).
- **气质**: 安静, 闭眼或半闭眼, 在反思.
- **场景定位**: 天际, 月旁 (只在夜场景出现, panel ④ 才显形).
- **EN gen keywords**: silver-gauze maiden in lotus seat, translucent, floating near a full moon, small slate tablet with reflection notes, eyes half-closed in meditation.

---

## 6. Do's and Don'ts

### ✓ Do

- 留白 (negative space) ≥ 30% of frame
- 朱砂红用在 < 5% 区域, 永远当 accent
- 题词用**毛笔行楷 / 楷书 / 篆书**, 不要 sans-serif
- 角色穿**汉服 / 道袍 / 长衫**, 多层叠穿, 飘逸感
- 道具用**毛笔 / 砚台 / 古卷 / 印章 / 灯笼 / 算盘 / 古籍**
- 灵气用**云雾 + 金线 + 水波**, 不要数据流粒子
- 建筑用**汉风飞檐亭阁** (岳阳楼 / 滕王阁结构), 不要日式神社/torii
- 自然元素: **松 / 仙鹤 / 锦鲤 / 山岚 / 溪水 / 月**

### ✗ Don't

- ❌ 日式动漫 (anime style, big sparkly eyes, chibi proportions)
- ❌ 樱花 / 和服 / 神社 / torii 鸟居
- ❌ 现代建筑 / 玻璃高楼 / 钢筋
- ❌ 电脑屏幕 / 手机 / 屏幕辉光
- ❌ 霓虹 / 赛博朋克 / 数据粒子炫光
- ❌ 西装革履 / 牛仔裤 / T 恤
- ❌ 3D 渲染感 / 塑料皮肤质感
- ❌ 中央对称构图 (这是基督教堂式, 太正面)
- ❌ 红色超过 5% 画面占比

---

## 7. Delivery Format Suggestion

When ready to commission / generate:

1. **Hero** (1 image): 16:9, 1920×1080 baseline, 3840×2160 ideal. PNG, lossless.
2. **Storyboard** (4 images): 4:5 each, 1024×1280. PNG. Provided as a single composite 4096×1280 for README ease-of-embed, plus individual files for X / 小红书 carousel.
3. **Character ID cards** (8 images): 3:4 portraits of each spirit, white background, 1024×1366. For docs/architecture and "Meet the agents" landing pages.

If using image-gen models, **fix a seed** for visual coherence across the set. Suggested workflow:
1. Generate Hero first, pick best variant.
2. Extract the dominant color seed + composition.
3. Feed back into storyboard prompts with `--seed N` (MJ) or matching parameters in 通义/智谱.
4. Generate character cards last, referencing Hero for skin-tone / robe color consistency.

---

## 8. Open Questions for Next Iteration

- [ ] 16 secondary spirits (mainline-classifier / market-scanner / morning-brief-writer etc.) — character motifs TBD
- [ ] Whether to include the **研究者本人** (the side-back scholar) as the user-avatar, or keep him deliberately faceless
- [ ] Dark-mode variant of Hero (moon-up night scene mirror of dusk Hero)?
- [ ] Animated GIF version for X / 微博 (Panel ② to ③ transition, focusing on the summoning)
- [ ] Logo treatment: should "觀瀾" sit as wordmark inside the painting, or floating above as a separate brand asset?

---

> *Brief v0.2 · 2026-05-25 · Style direction: 国风 (NOT 日式)*
