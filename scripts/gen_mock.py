#!/usr/bin/env python3
"""演示数据生成器。

设计原则：**结构真、数值假**。
分布形态按即时配送业务的真实规律来（时段双峰、距离对数正态、销量幂律、
供给逐级损耗、订单密度向商业中心衰减），数值全部由随机过程生成，
与任何真实业务无关。

固定随机种子，每次生成结果一致，保证演示可复现。
只依赖 Python 标准库，任何环境都能跑。

用法
    python3 scripts/gen_mock.py            # 生成到 site/data/
    python3 scripts/gen_mock.py --seed 7   # 换一组数
"""
import argparse
import datetime
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "data")

# ── 演示城市范围。底图与 POI 形态取自公开地图数据，
#    以下全部点位、围栏与业务数值均为虚构，不指向任何真实地点 ──
BBOX = {"s": 25.02, "w": 55.10, "n": 25.30, "e": 55.42}
CELL = 0.002          # 需求网格，约 222 m
ORIGIN_GRID = 0.005   # OD 起点网格，约 500 m

# ── 目标量级 ──────────────────────────────────────────────
# 演示数据向这组量级收敛，使各页面的数字读起来与真实即时配送业务同量级。
# 这里只锚定「量级」，具体数值仍由随机过程生成，与任何真实业务无关。
SCALE = {
    # ⚠ 这些是「量级」锚点，不是真实取值。刻意与任何真实业务的数字错开，
    #    只保证生成结果落在同一个数量级上，读起来有真实业务的体感。
    "window_days": 60,                 # 数据窗口
    "city_orders_window": 3_860_000,   # 窗口内全城平台完单
    "city_aov": 43.2,                  # 客单价
    "city_active_merchants": 11_800,   # 全城出单商家
    "poi_food": 3_640,                 # 公网餐饮 POI
    "poi_demand": 4_910,               # 公网需求侧 POI（住宅 写字楼 酒店 学校 公园 别墅）
    "free_delivery_share": 0.845,      # 免配占比
    "op_candidates": 44,               # 全域候选点位
    "op_emirates": 4,                  # 覆盖行政区数
}

# ── 名称池。全部为生成用的中性词，不对应任何真实商家或地点 ──
CUISINES = ["快餐", "咖啡", "烘焙", "亚洲菜", "烤串",
            "甜品", "饮品", "三明治", "披萨", "轻食"]
CUISINE_EN = {"快餐": "Fast Food", "咖啡": "Coffee", "烘焙": "Bakery", "亚洲菜": "Asian",
              "烤串": "Grill", "甜品": "Dessert", "饮品": "Beverage",
              "三明治": "Sandwich", "披萨": "Pizza", "轻食": "Healthy"}
NAME_A = ["Golden", "Urban", "Royal", "Fresh", "Sunny", "Silver", "Green",
          "Blue", "Spice", "Daily", "Corner", "Prime", "Little", "Grand",
          "Amber", "Copper", "Harbour", "Willow", "Cedar", "Lantern"]
NAME_B = ["Spoon", "Grill", "Kitchen", "House", "Table", "Garden", "Bowl",
          "Bites", "Roast", "Deli", "Bakery", "Brew", "Wok", "Oven",
          "Larder", "Pantry", "Counter", "Yard"]
ITEM_A = ["招牌", "秘制", "手作", "现烤", "香辣", "经典", "双人", "轻食",
          "冰镇", "慢炖", "厚切", "爆汁"]
ITEM_B = ["鸡排饭", "牛肉卷", "拿铁", "可颂", "沙拉", "炒面", "汉堡", "烤翅",
          "奶茶", "芝士挞", "拼盘", "浓汤", "披萨", "三明治", "布丁"]
AREA_A = ["Northbay", "Riverside", "Westgate", "Lakeview", "Old Mill", "Harbour",
          "Summit", "Foundry", "Orchard", "Beacon", "Quarry", "Lantern",
          "Cedar", "Willow", "Marble", "Copper"]
AREA_B = ["Heights", "Fields", "Gardens", "Park", "Quarter", "Green",
          "Row", "Terrace", "Point", "Reach"]

# ── 四个商圈。名称与坐标均为虚构 ──
DISTRICT_SEEDS = [
    ("nbc", "北湾商圈", "Northbay Central", 25.118, 55.381, 1.24,
     ["Willow Court", "Cedar Rise", "Orchard Walk"], "开航准备"),
    ("rvq", "河畔商圈", "Riverside Quarter", 25.187, 55.270, 0.61,
     ["Harbour Walk", "Marina Steps", "Palm Row", "Beacon Yard", "Summit Gardens"], "开航准备"),
    ("wgp", "西门商圈", "Westgate Plaza", 25.118, 55.200, 1.06,
     ["Larch Park", "Foundry Lane"], "线索"),
    ("lkv", "湖景商圈", "Lakeview Mall", 25.271, 55.317, 0.91,
     ["Lantern Square", "Quarry Green", "Bridgeway"], "开航准备"),
]
SUPPLY_RKM = 0.75     # 起飞点取餐半径，与页面默认值同源
LAND_RKM = 1.0        # 降落圈默认半径
PICK_RATE = 0.05      # 配送方式选择率（假定值）

SECTOR_COLORS = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
                 "#3498db", "#9b59b6", "#e84393", "#95a5a6"]


# ── 分布工具 ──────────────────────────────────────────────

def bimodal_hours(rng, total):
    """24 小时订单分布：午高峰 12-14、晚高峰 18-21 两个峰。

    真实外卖需求就是这个形状；平铺的均匀分布一眼就假。
    """
    weights = []
    for h in range(24):
        w = 0.4                                        # 全天底噪
        w += 3.2 * math.exp(-((h - 13.0) ** 2) / 2.2)  # 午峰
        w += 4.6 * math.exp(-((h - 19.5) ** 2) / 3.0)  # 晚峰，比午峰高
        if h < 8:
            w *= 0.15                                  # 凌晨基本没单
        w *= rng.uniform(0.88, 1.12)                   # 逐时抖动，否则低谷会是一条直线
        weights.append(w)
    s = sum(weights)
    base = [w / s for w in weights]
    out, left = [], total
    for i, p in enumerate(base):
        n = int(round(total * p)) if i < 23 else left
        n = max(0, min(n, left))
        out.append(n)
        left -= n
    return out


def lognormal_km(rng, mu=1.65, sigma=0.55):
    """配送距离：对数正态，中位数约 5.2 km，长尾到 15 km+。"""
    return round(math.exp(rng.gauss(mu, sigma)), 2)


def powerlaw_sales(rng, n, alpha=1.35, scale=210):
    """商家月销量：幂律长尾。头部几家吃掉大半销量，这才是真实的商家结构。"""
    vals = [int(scale * rng.paretovariate(alpha)) for _ in range(n)]
    return sorted(vals, reverse=True)


def jitter_point(rng, lat, lng, km):
    """在给定点周围 km 半径内随机取一点。"""
    r = km * math.sqrt(rng.random())
    th = rng.uniform(0, 2 * math.pi)
    return (round(lat + (r / 111.0) * math.cos(th), 5),
            round(lng + (r / (111.0 * math.cos(math.radians(lat)))) * math.sin(th), 5))


def haversine(a_lat, a_lng, b_lat, b_lng):
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(h)), 3)


def blob(rng, lat, lng, r_km, n=9, wobble=(0.7, 1.3)):
    """在一点周围画一个不规则多边形，当作围栏用。"""
    poly = []
    for i in range(n):
        th = 2 * math.pi * i / n
        rr = r_km * rng.uniform(*wobble)
        poly.append([round(lat + (rr / 111.0) * math.cos(th), 5),
                     round(lng + (rr / (111.0 * math.cos(math.radians(lat)))) * math.sin(th), 5)])
    return poly


def area_name(rng, used):
    for _ in range(60):
        n = "%s %s" % (rng.choice(AREA_A), rng.choice(AREA_B))
        if n not in used:
            used.add(n)
            return n
    n = "%s %s %d" % (rng.choice(AREA_A), rng.choice(AREA_B), len(used))
    used.add(n)
    return n


# ── 城市骨架：商业中心与热点 ─────────────────────────────

def gen_cores(rng):
    """几个商业核心 + 一批次级热点，订单密度围着它们衰减。

    真实城市的订单不是均匀铺开的，是几个核心加一串卫星。
    """
    cores = []
    for did, _, _, lat, lng, _, _, _ in DISTRICT_SEEDS:
        cores.append({"lat": lat, "lng": lng, "w": rng.uniform(2.4, 3.6), "r": rng.uniform(2.2, 3.4)})
    for _ in range(14):
        lat = rng.uniform(BBOX["s"] + 0.02, BBOX["n"] - 0.02)
        lng = rng.uniform(BBOX["w"] + 0.03, BBOX["e"] - 0.03)
        cores.append({"lat": lat, "lng": lng, "w": rng.uniform(0.5, 1.7), "r": rng.uniform(1.4, 3.0)})
    return cores


def density_at(cores, lat, lng):
    v = 0.0
    for c in cores:
        d = haversine(lat, lng, c["lat"], c["lng"])
        v += c["w"] * math.exp(-(d / c["r"]) ** 1.6)
    return v


# ── 商圈 ──────────────────────────────────────────────────

def gen_districts(rng):
    """四个商圈：一个起飞点 + 若干降落点 + 一圈自画的业务围栏。"""
    out = []
    for did, nm, hub, lat, lng, r, lands, status in DISTRICT_SEEDS:
        landings = []
        for i, ln in enumerate(lands):
            # 降落点落在起飞点 1.2–3.2 km 外，避免与取餐圈重叠灌进本地短单
            dist = rng.uniform(1.2, 3.2)
            p = jitter_point(rng, lat, lng, dist)
            while haversine(lat, lng, p[0], p[1]) < 1.15:
                p = jitter_point(rng, lat, lng, dist)
            landings.append({
                "id": "%s-L%d" % (did, i + 1), "name": ln,
                "lat": p[0], "lng": p[1], "rkm": LAND_RKM,
                "form": rng.choice(["cabinet", "cabinet", "winch"]),
                "leg_km": haversine(lat, lng, p[0], p[1]),
            })
        out.append({
            "id": did, "nm": nm, "hubName": hub, "model": "A1", "status": status,
            "hubLat": lat, "hubLng": lng, "supplyRkm": SUPPLY_RKM,
            "fence": blob(rng, lat, lng, r),
            "lands": landings,
            "pickRate": PICK_RATE,
            # 场地租金：起飞点按商场档位拉开，降落点按点数计
            "takeoffRent": int(rng.uniform(6200, 18500) / 100) * 100,
            "landingRentPerPt": 3000,
        })
    for d in out:
        d["landingRent"] = d["landingRentPerPt"] * len(d["lands"])
    return out


def gen_plan_zones(rng):
    """长航程索降的规划覆盖区：只做地图规划层，不做商圈、不参与测算。"""
    used = set()
    zones = []
    for _ in range(3):
        lat = rng.uniform(BBOX["s"] + 0.03, BBOX["n"] - 0.03)
        lng = rng.uniform(BBOX["w"] + 0.04, BBOX["e"] - 0.04)
        pts = []
        for k in range(rng.randint(2, 3)):
            p = jitter_point(rng, lat, lng, rng.uniform(0.6, 2.6))
            pts.append({"nm": area_name(rng, used), "lat": p[0], "lng": p[1],
                        "role": "起飞" if k == 0 else "降落"})
        zones.append({"nm": area_name(rng, used), "pts": pts})
    return zones


# ── 地图图层 ──────────────────────────────────────────────

def gen_pois(rng, cores):
    """公网 POI：供给侧（餐饮 + 三档商场）与需求侧六类。

    地图底图与 POI 取自公开数据，其上的点位与业务数值按同一形态生成。
    """
    def sample(n, pw=1.0):
        pts = []
        guard = 0
        while len(pts) < n and guard < n * 60:
            guard += 1
            lat = rng.uniform(BBOX["s"], BBOX["n"])
            lng = rng.uniform(BBOX["w"], BBOX["e"])
            d = density_at(cores, lat, lng)
            if rng.random() < min(1.0, (d / 3.2) ** pw):
                pts.append([round(lat, 5), round(lng, 5)])
        return pts

    supply = sample(SCALE["poi_food"] - 70, 0.9)
    malls = []
    for tier, n, pw in ((0, 18, 1.4), (1, 30, 1.0), (2, 22, 0.7)):
        for p in sample(n, pw):
            malls.append([p[0], p[1], tier])
    # 需求侧六类：住宅 酒店 写字楼 学校 公园 别墅
    shares = [0.37, 0.12, 0.29, 0.05, 0.03, 0.14]
    pws = [0.8, 1.3, 1.3, 0.6, 0.4, 0.35]
    demand = [sample(int(SCALE["poi_demand"] * s), w) for s, w in zip(shares, pws)]
    counts = {
        "supply": len(supply),
        "residential": len(demand[0]), "hotel": len(demand[1]),
        "office": len(demand[2]), "school": len(demand[3]),
        "park": len(demand[4]), "villa": len(demand[5]),
        "mallsLarge": sum(1 for m in malls if m[2] == 0),
        "mallsMedium": sum(1 for m in malls if m[2] == 1),
        "mallsSmall": sum(1 for m in malls if m[2] == 2),
    }
    return {"supply": supply, "demand": demand, "malls": malls, "counts": counts}


def gen_sectors(rng):
    """九个大区 + 一批社区面。区名为生成名，不指向真实行政区。

    分区按 3×3 网格切块并向内收缩留出间隙，四角固定、每边中点轻微起伏 ——
    真实城市的行政分区本就是沿主干道与水系划的块状，用大半径不规则多边形去画，
    相邻区必然交叠成一团，地图会糊。
    """
    used = set()
    sectors, communities = [], []
    cols = rows = 3
    dh = (BBOX["n"] - BBOX["s"]) / rows
    dw = (BBOX["e"] - BBOX["w"]) / cols
    inset = 0.07                      # 块间留白，避免边界压边界
    ah, aw = dh * 0.05, dw * 0.05     # 边中点起伏幅度

    def jit(v, amp):
        return round(v + rng.uniform(-amp, amp), 6)

    for i in range(9):
        cx, cy = i % cols, i // cols
        s0 = BBOX["s"] + dh * cy + dh * inset
        n0 = BBOX["s"] + dh * (cy + 1) - dh * inset
        w0 = BBOX["w"] + dw * cx + dw * inset
        e0 = BBOX["w"] + dw * (cx + 1) - dw * inset
        mlat, mlng = (s0 + n0) / 2, (w0 + e0) / 2
        poly = [
            [round(s0, 6), round(w0, 6)],
            [jit(s0, ah), round(mlng, 6)],
            [round(s0, 6), round(e0, 6)],
            [round(mlat, 6), jit(e0, aw)],
            [round(n0, 6), round(e0, 6)],
            [jit(n0, ah), round(mlng, 6)],
            [round(n0, 6), round(w0, 6)],
            [round(mlat, 6), jit(w0, aw)],
        ]
        sectors.append({
            "name": "分区 %d · %s" % (i + 1, area_name(rng, used)),
            "color": SECTOR_COLORS[i], "poly": poly,
            "centroid": [round(mlat, 5), round(mlng, 5)],
        })
        # 社区面：每区 4 到 7 个，且收在本区块内，不跨界
        for _ in range(rng.randint(4, 7)):
            plat = rng.uniform(s0 + dh * 0.12, n0 - dh * 0.12)
            plng = rng.uniform(w0 + dw * 0.12, e0 - dw * 0.12)
            communities.append({"poly": blob(rng, plat, plng, rng.uniform(0.45, 1.0), n=6),
                                "color": SECTOR_COLORS[i]})
    return sectors, communities


def gen_villa_zones(rng):
    used = set()
    out = []
    for _ in range(12):
        lat = rng.uniform(BBOX["s"] + 0.01, BBOX["n"] - 0.01)
        lng = rng.uniform(BBOX["w"] + 0.02, BBOX["e"] - 0.02)
        out.append({"name": area_name(rng, used),
                    "poly": blob(rng, lat, lng, rng.uniform(0.5, 1.2), n=8)})
    return out


def gen_nfz(rng):
    """禁飞与限飞分区。编号与范围均为生成值，不对应任何真实空域公告。"""
    def zone(prefix, i, r):
        lat = rng.uniform(BBOX["s"] + 0.02, BBOX["n"] - 0.02)
        lng = rng.uniform(BBOX["w"] + 0.03, BBOX["e"] - 0.03)
        return {"name": "%s-%02d" % (prefix, i + 1),
                "coords": blob(rng, lat, lng, r, n=7, wobble=(0.75, 1.25))}
    return {
        # 数量照真实空域公告的量级，原先 32 个叠在一起会把底图糊死
        "pink": [zone("NFZ", i, rng.uniform(1.2, 2.4)) for i in range(3)],
        "red": [zone("RSA", i, rng.uniform(0.8, 1.8)) for i in range(5)],
        "beige": [zone("NOT", i, rng.uniform(1.0, 2.0)) for i in range(4)],
    }


def gen_drone_candidates(rng, cores, nfz):
    """算法测算的潜在起降点：只作选址灵感参考，不参与测算。"""
    def pip(lat, lng, poly):
        inside = False
        n = len(poly)
        j = n - 1
        for i in range(n):
            yi, xi = poly[i]
            yj, xj = poly[j]
            if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
        return inside

    hard = [z["coords"] for z in nfz["pink"]] + [z["coords"] for z in nfz["red"]]

    def pick(n, pw):
        out = []
        guard = 0
        while len(out) < n and guard < n * 200:
            guard += 1
            lat = rng.uniform(BBOX["s"], BBOX["n"])
            lng = rng.uniform(BBOX["w"], BBOX["e"])
            if rng.random() >= min(1.0, (density_at(cores, lat, lng) / 3.2) ** pw):
                continue
            if any(pip(lat, lng, z) for z in hard):
                continue          # 禁飞区硬过滤
            out.append([round(lat, 4), round(lng, 4), round(rng.uniform(0.4, 1.0), 2)])
        return out

    hubs = pick(40, 1.4)
    bldg_pts = pick(SCALE["op_candidates"], 1.0)
    villa_pts = pick(27, 0.4)
    routes = []
    for h in hubs:
        near = [p for p in bldg_pts + villa_pts if haversine(h[0], h[1], p[0], p[1]) <= 5.0]
        rng.shuffle(near)
        for p in near[:8]:
            routes.append([h[0], h[1], p[0], p[1]])
    return {"hubs": hubs, "portsBldg": bldg_pts, "portsVilla": villa_pts, "routes": routes}


# ── 需求网格与订单 ───────────────────────────────────────

def gen_demand_grid(rng, cores, pois):
    """需求网格：一格 222 m，带 24 小时数组。订单总量向 SCALE 收敛。"""
    cells = {}
    # 以需求侧 POI 播种，再按核心密度加权，避免生成一张均匀的噪声图
    for group in pois["demand"]:
        for p in group:
            gy = int((p[0] - BBOX["s"]) / CELL)
            gx = int((p[1] - BBOX["w"]) / CELL)
            key = (gy, gx)
            c = cells.get(key)
            if c is None:
                lat = round(BBOX["s"] + (gy + 0.5) * CELL, 5)
                lng = round(BBOX["w"] + (gx + 0.5) * CELL, 5)
                c = cells[key] = {"lat": lat, "lng": lng, "poi": 0,
                                  "d": density_at(cores, lat, lng)}
            c["poi"] += 1
    raw = []
    for c in cells.values():
        v = (c["poi"] * 0.75 + c["d"] * 1.3) * rng.uniform(0.55, 1.55)
        if v <= 0.02:
            continue
        c["raw"] = v
        raw.append(c)
    tot_raw = sum(c["raw"] for c in raw) or 1.0
    k = SCALE["city_orders_window"] / tot_raw
    out, hourly = [], [0] * 24
    for c in raw:
        n = int(round(c["raw"] * k))
        if n < 1:
            continue
        hrs = bimodal_hours(rng, n)
        for i, v in enumerate(hrs):
            hourly[i] += v
        out.append([c["lat"], c["lng"], n, hrs])
    return out, hourly


def gen_shops(rng, cores, pois):
    """全城出单商家：坐标随餐饮 POI，销量幂律，品类与品牌为生成值。"""
    n = SCALE["city_active_merchants"]
    base = pois["supply"]
    sales = powerlaw_sales(rng, n, alpha=1.32, scale=95)
    brands = ["%s %s" % (rng.choice(NAME_A), rng.choice(NAME_B)) for _ in range(420)]
    out = []
    for i in range(n):
        if i < len(base):
            lat, lng = base[i]
        else:
            src = base[rng.randrange(len(base))]
            lat, lng = jitter_point(rng, src[0], src[1], 0.35)
        cat = rng.choice(CUISINES)
        # 有效商家：配送覆盖 + 高峰营业 + 近 30 天有动销
        valid = 1 if rng.random() < 0.62 else 0
        brand = rng.choice(brands) if rng.random() < 0.34 else ""
        sku = rng.randint(8, 140)
        out.append([lat, lng, valid, cat, brand, sku, sales[i]])
    return out


def gen_flows(rng, demand, shops):
    """订单流向：取货点 → 收货点的粗聚合弧线，只画头部若干条。"""
    top_s = sorted(shops, key=lambda s: -s[6])[:600]
    top_d = sorted(demand, key=lambda c: -c[2])[:600]
    out = []
    for _ in range(300):
        s = top_s[rng.randrange(len(top_s))]
        d = top_d[rng.randrange(len(top_d))]
        km = haversine(s[0], s[1], d[0], d[1])
        if km < 0.4 or km > 9.0:
            continue
        w = int(d[2] * rng.uniform(0.02, 0.12))
        if w < 200:
            continue
        out.append([round(s[0], 3), round(s[1], 3), round(d[0], 3), round(d[1], 3), w])
    out.sort(key=lambda f: -f[4])
    return out[:300]


def gen_od(rng, demand, shops, districts):
    """OD 起讫矩阵：按起点网格聚合，每个起点给出若干终点格与单量。

    页面按「起点落在取餐圈内 且 终点落在某个降落圈内」配对，
    所以这里的起点必须覆盖到各商圈取餐圈，终点覆盖到降落圈周边。
    """
    # 起点格：有餐饮商家的格子
    origins = {}
    for s in shops:
        i = round(s[0] / ORIGIN_GRID)
        j = round(s[1] / ORIGIN_GRID)
        o = origins.setdefault((i, j), {"lat": round(i * ORIGIN_GRID, 5),
                                        "lng": round(j * ORIGIN_GRID, 5), "w": 0})
        o["w"] += s[6]
    # 终点格：需求网格
    dest = [(c[0], c[1], c[2]) for c in demand]
    dest.sort(key=lambda x: -x[2])

    by_cell = {}
    total = 0
    for (i, j), o in origins.items():
        near = []
        for dlat, dlng, dv in dest:
            km = haversine(o["lat"], o["lng"], dlat, dlng)
            if km < 0.35 or km > 6.0:
                continue
            # 距离衰减：近处成单概率高，远处长尾
            wgt = dv * math.exp(-(km / 2.6) ** 1.35)
            if wgt <= 0:
                continue
            near.append((wgt, dlat, dlng))
        if not near:
            continue
        near.sort(reverse=True)
        near = near[:45]
        wsum = sum(x[0] for x in near) or 1.0
        # 该起点的总外发量 ∝ 商家销量，60 天窗口
        vol = o["w"] * SCALE["window_days"] / 30.0 * rng.uniform(0.7, 1.3)
        rows = []
        for wgt, dlat, dlng in near:
            cnt = int(round(vol * wgt / wsum))
            if cnt < 2:
                continue
            rows.append([dlat, dlng, cnt])
            total += cnt
        if rows:
            by_cell["%d,%d" % (i, j)] = rows
    return {
        "window_days": SCALE["window_days"],
        "origin_grid": ORIGIN_GRID, "dest_grid": CELL,
        "cnt_min": 2, "total": total, "byCell": by_cell,
    }


# ── 商家与商品 ───────────────────────────────────────────

def gen_merchants(rng, districts, shops):
    """商圈内商家名单：签约四级漏斗 + 无人机渠道状态。

    四级：圈内商家池 → 已签 → 已开通无人机配送 → 近 30 天有动销。
    逐级都有损耗，签约最难、动销最差，这个形状是这套业务的真问题。
    """
    out = []
    mid = 0
    for d in districts:
        lat, lng = d["hubLat"], d["hubLng"]
        # 取餐圈内的真实商家先纳入，不够再按密度补齐
        inr = [s for s in shops if haversine(lat, lng, s[0], s[1]) <= d["supplyRkm"]]
        inr.sort(key=lambda s: -s[6])
        n = max(205, min(320, len(inr)))
        if len(inr) < n:
            for _ in range(n - len(inr)):
                p = jitter_point(rng, lat, lng, d["supplyRkm"])
                inr.append([p[0], p[1], 1 if rng.random() < 0.6 else 0,
                            rng.choice(CUISINES), "", rng.randint(8, 90),
                            int(60 * rng.paretovariate(1.4))])
        inr = inr[:n]
        r_sign = rng.uniform(0.12, 0.22)      # 签约率：一线最难的一级
        r_open = rng.uniform(0.45, 0.72)      # 签了之后真开通无人机配送
        r_active = rng.uniform(0.34, 0.62)    # 开通之后真有动销
        for s in inr:
            mid += 1
            signed = rng.random() < r_sign
            signing = (not signed) and rng.random() < 0.055
            opened = signed and rng.random() < r_open
            active = opened and rng.random() < r_active
            sku = s[5]
            listed = int(sku * rng.uniform(0.05, 0.55)) if opened else 0
            off = rng.randint(1, 4) if (opened and not active and rng.random() < 0.35) else 0
            pay = int(s[6] * rng.uniform(0.4, 1.1)) if active else 0
            rc = int(pay * rng.uniform(0, 0.09)) if pay > 40 else 0
            # GTV 与订单量正相关但不线性：客单价随店的量级缓升（大店客单更高），再叠一层噪声。
            gtv = int(s[6] * (24 + 13 * math.log10(max(10, s[6]))) * rng.uniform(0.82, 1.24))
            # 履约率：主群落在 94–99，尾部拖到 80 出头。半正态取反 + 一成的长尾扰动。
            fulfill = round(max(78.0, 99.6 - abs(rng.gauss(0, 2.4))
                               - (rng.uniform(3, 12) if rng.random() < 0.11 else 0)), 1)
            # 无人机完单：动销店才有，且只占该店大盘订单的极小一片。
            d_ord = max(1, int(s[6] * rng.uniform(0.004, 0.02))) if active else 0
            # 真出过单的 SKU 数：不会超过在架数，也不会超过完单数。
            d_sold = min(max(0, listed - off), max(1, int(d_ord * rng.uniform(0.4, 0.9)))) if active else 0
            # 复购率：集中在两成上下的中段。写成率而不是人数——单店完单只有个位数时，
            # 人数取整会把 20% 抹成 0，环上的复购率就永远读作接近零。
            repeat_r = round(min(48.0, max(4.0, rng.gauss(21, 6.5))), 1) if active else 0
            out.append({
                "id": "m%05d" % mid,
                "n": "%s %s" % (rng.choice(NAME_A), rng.choice(NAME_B)),
                "unit": d["id"], "cat": s[3],
                "lat": s[0], "lng": s[1],
                "dist_km": haversine(lat, lng, s[0], s[1]),
                "o": s[6], "g": gtv, "fulfill": fulfill,
                "dOrd": d_ord, "repeatR": repeat_r,
                "st": "signed" if signed else ("signing" if signing else "open"),
                "dOpen": 1 if opened else 0,
                "active": bool(active),
                "lst": {"sku": sku, "dsku": listed},
                "dSku": {"on": max(0, listed - off), "off": off, "sold": d_sold},
                "warn": "zero" if (signed and not active) else "",
                "afs": ({"pay": pay, "rc": rc, "rr": 0,
                         "rate": round(rc / pay * 100, 1)} if pay > 40 else None),
            })
    return out


def gen_items(rng, merchants):
    """商品：每家店取前三个高销品，汇成商圈的商品名单。

    四级：圈内在售 → 已签商家可供给 → 已上无人机菜单 → 有动销。
    """
    out = []
    sid = 0
    for m in merchants:
        n = 3 if m["o"] > 30 else 2
        for k in range(n):
            sid += 1
            o = int(m["o"] * rng.uniform(0.08, 0.34) / (k + 1))
            supply = m["st"] == "signed"
            listed = supply and m["dOpen"] == 1 and rng.random() < 0.82
            moving = listed and rng.random() < 0.28
            # 渠道回撤：这家店有被关掉的 SKU 时，它卖过的商品里会有一部分当前已不在菜单上。
            # 「卖过但现在关了」是需求已被证实的那一档，工作台单独打标，故数据里要留得下这个状态。
            if moving and m["dSku"]["off"] and rng.random() < 0.5:
                listed = False
            out.append({
                "id": "i%06d" % sid,
                "n": "%s%s" % (rng.choice(ITEM_A), rng.choice(ITEM_B)),
                "shop": m["id"], "shopName": m["n"], "unit": m["unit"], "cat": m["cat"],
                "o": o, "supply": supply, "listed": listed,
                "moving": moving,
            })
    # 分位：按商圈内累计订单排名。前 20% 记为高销品
    for unit in {i["unit"] for i in out}:
        rows = [i for i in out if i["unit"] == unit]
        rows.sort(key=lambda x: -x["o"])
        n = len(rows) or 1
        for idx, r in enumerate(rows):
            r["pp"] = round((1 - idx / n) * 100, 1)
    return out


# ── 逐商圈上下文 ─────────────────────────────────────────

def district_context(rng, districts, od, merchants, items):
    """逐商圈：订单池、商家覆盖、商品覆盖、当前实测日均、逐点池子构成。

    口径与选址沙盘完全一致（取餐圈内起点 × 降落圈内终点，配送方式选择率 5%），
    沙盘的输出就是单元经济台的输入，两页数字必须对得上。
    """
    og = od["origin_grid"]
    out = {}
    for d in districts:
        hub_lat, hub_lng = d["hubLat"], d["hubLng"]
        sr = d["supplyRkm"]
        # 真 OD 配对：起点落在取餐圈、终点落在某个降落圈，按最近降落点归属，不重复计
        pools = [{"share": 0.0, "bands": [0.0, 0.0]} for _ in d["lands"]]
        total = 0
        ci = round(hub_lat / og)
        cj = round(hub_lng / og)
        span = int(sr / 111.0 / og) + 2
        for i in range(ci - span, ci + span + 1):
            for j in range(cj - span, cj + span + 1):
                rows = od["byCell"].get("%d,%d" % (i, j))
                if not rows:
                    continue
                olat, olng = round(i * og, 5), round(j * og, 5)
                if haversine(olat, olng, hub_lat, hub_lng) > sr:
                    continue
                for dlat, dlng, cnt in rows:
                    best, bi = 1e9, -1
                    for li, L in enumerate(d["lands"]):
                        km = haversine(dlat, dlng, L["lat"], L["lng"])
                        if km <= L["rkm"] and km < best:
                            best, bi = km, li
                    if bi < 0:
                        continue
                    pools[bi]["share"] += cnt
                    pools[bi]["bands"][0 if best <= 1.0 else 1] += cnt
                    total += cnt
        monthly = int(round(total / SCALE["window_days"] * 30))

        inr = [m for m in merchants if m["unit"] == d["id"]]
        ord_all = sum(m["o"] for m in inr) or 1
        signed = [m for m in inr if m["st"] == "signed"]
        signing = [m for m in inr if m["st"] == "signing"]
        cov_now = sum(m["o"] for m in signed) / ord_all
        cov_near = sum(m["o"] for m in signed + signing) / ord_all
        # 目标档：把累计订单占比到 80% 的头部商家全签下来
        rows = sorted(inr, key=lambda m: -m["o"])
        acc, cov_target = 0, 0.0
        for m in rows:
            acc += m["o"]
            cov_target = acc / ord_all
            if cov_target >= 0.80:
                break

        # 商品覆盖：高销品里已上无人机菜单的 ÷ 已签商家可供给的
        hi = [i for i in items if i["unit"] == d["id"] and i["pp"] >= 80]
        hi_total = len(hi) or 1
        sup_n = sum(1 for i in hi if i["supply"])
        lst_n = sum(1 for i in hi if i["listed"])
        rate = (lst_n / sup_n) if sup_n else 0.0

        legs = [L["leg_km"] for L in d["lands"]]
        route_dist = int(sum(legs) / len(legs) * 1000) if legs else 2500
        out[d["id"]] = {
            "nm": d["nm"], "hubName": d["hubName"], "model": d["model"], "status": d["status"],
            "hubLat": hub_lat, "hubLng": hub_lng,
            "lands": len(d["lands"]), "landNames": [L["name"] for L in d["lands"]],
            "mo": monthly, "pickRate": d["pickRate"],
            "routeDist": max(600, min(6000, route_dist)),
            "takeoffRent": d["takeoffRent"], "landingRent": d["landingRent"],
            "landingPools": [{"share": round(p["share"], 1),
                              "bands": [round(p["bands"][0], 1), round(p["bands"][1], 1)]}
                             for p in pools] if total > 0 else None,
            "supply": {
                "src": "签约表每日线",
                "shops": len(inr), "signed": len(signed), "signing": len(signing),
                "tosign": len(inr) - len(signed) - len(signing),
                "coverage": {"now": round(cov_now, 4), "near": round(cov_near, 4),
                             "target": round(cov_target, 4), "default": "near"},
                "itemCov": {"tier": "CR80", "gran": "item", "hi_total": hi_total,
                            "signed_supply": sup_n, "listed": lst_n,
                            "rate": round(rate, 4),
                            "rate_raw": round(lst_n / hi_total, 4),
                            "note": "高销品已上无人机菜单 ÷ 已签商家可供给"},
            },
        }
    return out


# ── UE 参数与引擎镜像 ────────────────────────────────────
# 与 engine/ue_model.js 的 DEFAULTS 同源。经营门户的结论必须由脚本算出来，
# 不接受手工填写，所以这里要有一份能跑的引擎镜像。改公式两处同改。

UE_DEFAULTS = {
    "takeoffPts": 1, "landingPts": 4, "routeDist": 2500, "dailyOrders": 200,
    "peakShare": 0.12, "lastMileLandingPts": 2, "lastMileShare": 0.30,
    "aov": 52, "commissionRate": 0.18, "platformFee": 2, "deliveryFee": 11,
    "marketing": 8000, "subsidyPerOrder": 0,
    "takeoffRent": 9000, "landingRent": 12000,
    "takeoffFitoutPerPt": 3500, "landingFitoutPerPt": 900,
    "annualLeaveDays": 30, "weeklyOffDays": 52,
    "pilotProd": 280, "maintProd": 460, "groundProd": 110, "pickupProd": 80,
    "lastMileProd": 32, "patrolProd": 180, "patrolMinOrders": 180, "groundCapPerTakeoff": 4,
    "salPilot": 9200, "salMaint": 8600, "salPickup": 3400, "salGround": 4500,
    "salLastMile": 3400, "salPatrol": 3400,
    "droneSpeed": 10, "takeoffLandingMin": 4, "droneIdle": 0.3, "maxDrones": 24,
    "markerPerTakeoff": 6, "cabinetPerOrders": 150, "cabinetMaxPerTakeoff": 4,
    "chargingPerCabinet": 2, "storagePerDrones": 2,
    "depDrone": 640, "depMarker": 45, "depPort": 2900, "depCabinet": 300,
    "depCharging": 120, "depStorage": 85, "depSpare": 115, "depBoxReturn": 44,
    "batteryPrice": 1700, "batteryCycleLife": 600, "batteryPackEnergy": 390,
    "elecCost": 0.41, "coulombic": 0.93, "baseConsume": 0.35, "marginalConsume": 0.08,
    "baselineDist": 1700, "batterySafety": 0.75,
    "equipOM": 1000, "opsSupplies": 900, "simDrone": 580, "simPort": 580, "simHandheld": 480,
    "vehiclePerTakeoff": 600, "boxPerOrder": 0.3, "droneInsurance": 130, "rtkPerTakeoff": 200,
    "venueElecPerUnit": 300, "broadband": 1000, "networkOM": 1000, "daysPerMonth": 30,
    "groundStaff": True, "roiMultiple": 2, "lastMileSwitch": "auto", "equipDepOn": True,
}
SUBSIDY_STEADY = 2.5
GROWTH_DEF = {"orders": [0, 0.12, 0.12, 0.10, 0.10],
              "salary": 0.03, "aov": 0.03, "marketing": 0.10}


def ue_compute(o):
    p = dict(UE_DEFAULTS)
    p.update(o or {})
    rest = 365 / (365 - p["annualLeaveDays"] - p["weeklyOffDays"])
    mo = p["dailyOrders"] * p["daysPerMonth"]
    peak = p["dailyOrders"] * p["peakShare"]
    rpo = p["aov"] * p["commissionRate"] + p["platformFee"] + p["deliveryFee"]
    rpo_net = rpo - p["subsidyPerOrder"]

    cyc = p["routeDist"] * 2 / p["droneSpeed"] / 60 + p["takeoffLandingMin"]
    tph = 60 / cyc
    peak_drones = math.ceil(peak / tph) if peak > 0 else 0
    drones = min(math.ceil(peak_drones * (1 + p["droneIdle"])), p["maxDrones"] * p["takeoffPts"])

    n_pilot = math.ceil(p["dailyOrders"] / p["pilotProd"]) * rest
    n_maint = math.ceil(p["dailyOrders"] / p["maintProd"]) * rest
    n_pick = math.ceil(p["dailyOrders"] / p["pickupProd"]) * rest
    n_gnd = min(math.ceil(p["dailyOrders"] / p["groundProd"]),
                p["groundCapPerTakeoff"] * p["takeoffPts"]) * rest
    lm_daily = p["dailyOrders"] * p["lastMileShare"]
    n_lm = max(p["lastMileLandingPts"], math.ceil(lm_daily / p["lastMileProd"])) * rest
    n_pat = 0 if p["dailyOrders"] < p["patrolMinOrders"] else math.ceil(p["dailyOrders"] / p["patrolProd"]) * rest
    c_lm_raw, c_pat_raw = n_lm * p["salLastMile"], n_pat * p["salPatrol"]
    lm_cost = c_lm_raw + c_pat_raw
    lm_lever = mo * p["lastMileShare"] * rpo_net
    lm_roi = lm_lever / lm_cost if lm_cost > 0 else float("inf")
    gs = 1 if p["groundStaff"] else 0
    lm_on = gs and (1 if p["lastMileSwitch"] == "on"
                    else 0 if p["lastMileSwitch"] == "off"
                    else (1 if lm_roi >= p["roiMultiple"] else 0))
    labor = (n_pilot * p["salPilot"] + n_maint * p["salMaint"] + n_pick * p["salPickup"] * gs
             + n_gnd * p["salGround"] + c_lm_raw * lm_on + c_pat_raw * lm_on)

    n_cab = min(math.ceil(p["dailyOrders"] / p["cabinetPerOrders"]),
                p["cabinetMaxPerTakeoff"] * p["takeoffPts"])
    equip = (drones * p["depDrone"] + p["takeoffPts"] * p["markerPerTakeoff"] * p["depMarker"]
             + p["landingPts"] * p["depPort"] + n_cab * p["depCabinet"]
             + n_cab * p["chargingPerCabinet"] * p["depCharging"]
             + math.ceil(drones / p["storagePerDrones"]) * p["depStorage"]
             + p["takeoffPts"] * p["depSpare"] + p["landingPts"] * p["depBoxReturn"])
    equip = equip if p["equipDepOn"] else 0

    cr = p["baseConsume"] + (p["routeDist"] / 1000 - p["baselineDist"] / 1000) * p["marginalConsume"]
    battery = (cr / p["batterySafety"] * (p["batteryPrice"] / p["batteryCycleLife"]) * mo
               + cr * p["batteryPackEnergy"] / p["coulombic"] * p["elecCost"] / 1000 * mo)

    rent = p["takeoffRent"] + p["landingRent"]
    fitout = (p.get("takeoffFitoutM", p["takeoffPts"] * p["takeoffFitoutPerPt"])
              + p.get("landingFitoutM", p["landingPts"] * p["landingFitoutPerPt"]))
    venue = rent + fitout + p["venueElecPerUnit"] * (p["landingPts"] + p["takeoffPts"]) + p["broadband"]

    n_hand = math.ceil(n_gnd + n_pick)
    box = mo * p["boxPerOrder"]
    other = (p["equipOM"] * p["takeoffPts"] + p["opsSupplies"] * p["takeoffPts"]
             + math.ceil(drones) * p["simDrone"] + p["landingPts"] * p["simPort"]
             + n_hand * p["simHandheld"] + p["takeoffPts"] * p["vehiclePerTakeoff"]
             + box + drones * p["droneInsurance"] + p["takeoffPts"] * p["rtkPerTakeoff"])

    subsidy = p["subsidyPerOrder"] * mo
    cost = labor + equip + battery + venue + other + p["networkOM"] + subsidy
    revenue = rpo * mo + p["marketing"]
    profit = revenue - cost
    return {
        "revPerOrder": rpo, "monthlyOrders": mo, "revenue": revenue, "cost": cost,
        "profit": profit, "margin": profit / revenue if revenue else 0,
        "labor": labor, "equip": equip, "battery": battery, "venue": venue,
        "other": other, "subsidy": subsidy, "rent": rent, "drones": drones,
        "rentCeiling": revenue - (cost - rent),
        "costPerOrder": cost / mo if mo else 0,
    }


def ue_breakeven(cfg, cap=1500):
    """保本线取最后一段连续为正的起点。利润对单量非单调，首次穿零未必稳得住。"""
    prof = []
    for d in range(1, cap + 1):
        q = dict(cfg)
        q["dailyOrders"] = d
        prof.append(ue_compute(q)["profit"])
    first = next((i + 1 for i, v in enumerate(prof) if v >= 0), None)
    stable = None
    if prof[-1] >= 0:
        i = cap - 1
        while i >= 0 and prof[i] >= 0:
            i -= 1
        stable = i + 2
    return first, stable


def unit_cfg(ctx):
    """商圈上下文 → 引擎入参。商圈没有商业化数据，广告收入按 0，不替商圈创造数据。"""
    return dict(UE_DEFAULTS, **{
        "landingPts": max(1, ctx["lands"]), "routeDist": ctx["routeDist"],
        "takeoffRent": ctx["takeoffRent"], "landingRent": ctx["landingRent"],
        "marketing": 0, "subsidyPerOrder": SUBSIDY_STEADY,
        "takeoffFitoutM": 3500, "landingFitoutM": max(1, ctx["lands"]) * 900,
    })


def servable_rate(ctx):
    """可承接率 = 商家覆盖 × 商品覆盖。

    商品覆盖乘的是条件比率（已上架 ÷ 已签可供给），不是 已上架 ÷ 圈内高销品 ——
    后者分母含未签店、本身已经包含商家覆盖，再乘一次会把商家覆盖算两遍。
    商家覆盖是假定值的商圈不叠乘：假定值 × 实测值得到的是假精确。
    """
    cov = ctx["supply"]["coverage"]
    ic = ctx["supply"]["itemCov"]
    near = cov.get(cov.get("default", "near"))
    if near is None:
        return 0.4, "assumed"
    if ic and ic.get("rate"):
        return round(near * ic["rate"], 4), "near×item"
    return near, "near"


def gen_ue_params(districts, ctx):
    return {
        "_note": "全部为虚构演示参数，与任何真实业务无关",
        "defaults": UE_DEFAULTS,
        "subsidy_tiers": {"steady": SUBSIDY_STEADY, "firstYear": 7.5, "firstHalf": 10},
        "growth": GROWTH_DEF,
        "loss_cap_annual": 400000,
        "roi_multiple": 2,
        "districts": ctx,
    }


# ── 经营门户 ─────────────────────────────────────────────

def gen_deck(rng, ctx, rollout):
    """经营门户的数据源：三问结论、四支柱能力、指标三态、里程碑、迭代日志。

    三问的结论由这里按最新数据算出来，页面只负责呈现，不做业务计算。
    日期以生成当天为锚点往前推，构建时现算，避免演示站长期挂着一个陈旧日期。
    """
    today = datetime.date.today()
    day = lambda n: (today - datetime.timedelta(days=n)).isoformat()

    econ = []
    for did, c in ctx.items():
        acc, src = servable_rate(c)
        daily = round(c["mo"] * acc * c["pickRate"] / 30, 1)
        cfg = unit_cfg(c)
        now = ue_compute(dict(cfg, dailyOrders=max(1, daily)))
        first, be = ue_breakeven(cfg)
        econ.append({
            "id": did, "nm": c["nm"], "daily_now": daily, "servable": acc, "servable_src": src,
            "pool_monthly": c["mo"], "breakeven": be, "first_cross": first,
            "gap": round(max(0, (be or 0) - daily), 1),
            "profit_now": round(now["profit"]), "rent_now": round(now["rent"]),
            "rent_ceiling": round(now["rentCeiling"]),
        })
    worst = min(econ, key=lambda e: -(e["gap"] or 0))

    # 二问：开航推进到哪。五段阶段，逐商圈停在不同位置
    STAGES = ["选址", "签约", "部署", "路测", "开航"]
    launch = []
    for i, (did, c) in enumerate(ctx.items()):
        at = [3, 2, 1, 2][i % 4]
        stalled = rng.randint(2, 34)
        steps = [{"name": s,
                  "state": "done" if j < at else ("doing" if j == at else "todo"),
                  "date": day(150 - j * 26 - rng.randint(0, 6)) if j <= at else None}
                 for j, s in enumerate(STAGES)]
        launch.append({"id": did, "nm": c["nm"], "stage": STAGES[at], "stage_idx": at,
                       "steps": steps, "updated": day(stalled), "stalled_days": stalled,
                       "tasks_stalled": rng.randint(0, 6)})

    # 三问：结论确定性。驱动结论的每个量标一种数据性质
    metrics = [
        ["圈内平台订单池", "fact", "订单起讫网格按取餐圈与降落圈配对"],
        ["商家覆盖率", "fact", "签约状态逐店汇总，按订单加权"],
        ["商品覆盖率", "fact", "高销品上架状态逐条汇总"],
        ["签约四级漏斗", "fact", "商家池到动销逐级计数"],
        ["履约时长偏差", "fact", "承诺时长与实际时长之差"],
        ["保本线", "derived", "扫满产能区间取最后一段连续为正的起点"],
        ["可承受租金上限", "derived", "由收益反推，随单量变动"],
        ["月利润与利润率", "derived", "收入减成本合计"],
        ["机队规模与用工人数", "derived", "按峰值单量与人效推"],
        ["配送方式选择率", "assumed", "缺无人机实单样本，暂取 5%"],
        ["客单价与费率", "assumed", "参数文件给定"],
        ["单均营销补贴", "assumed", "参数文件给定，随站龄递减"],
        ["人均月薪与取餐人效", "assumed", "参数文件给定"],
        ["单机折旧与峰值架次", "assumed", "参数文件给定"],
        ["场地租金", "assumed", "签约前拿不到报价，走反向倒推"],
        ["五年逐年涨幅", "assumed", "未经校准，只用于看结构"],
    ]
    metrics = [{"name": n, "state": s, "note": t} for n, s, t in metrics]
    blocked = [m for m in metrics if m["state"] == "assumed"]

    pillars = [
        {"id": "PRE", "en": "PRE-LAUNCH", "zh": "开航前", "note": "① 开在哪 → ② 经济性 → ③ 排期",
         "mods": ["A", "B", "F"]},
        {"id": "POST", "en": "POST-LAUNCH", "zh": "开航后", "note": "① 供给作业 → ② 助手",
         "mods": ["D", "E"]},
        {"id": "HUB", "en": "COMMAND", "zh": "决策中枢", "note": "项目总览", "mods": ["C"]},
        {"id": "BASE", "en": "FOUNDATION", "zh": "底座", "note": "数据与口径", "mods": ["G"]},
    ]
    modules = {
        "A": {"code": "A", "zh": "选址沙盘", "en": "SITE SANDBOX", "flow": "开航① 开在哪",
              "link": "../sandbox/", "caps": [
                  {"nm": "圈选与需求洞察", "st": "done", "pr": "P0", "ai": "hybrid", "pg": 100,
                   "impl": "地图圈定取餐圈与降落圈，按订单起讫配对出圈内订单池",
                   "feas": "已可用，订单起讫网格已接入", "deps": "订单起讫聚合 · 商家库"},
                  {"nm": "测算链路", "st": "doing", "pr": "P0", "ai": "hybrid", "pg": 80,
                   "impl": "订单池 × 可承接率 × 选择率 → 日均单量 → 引擎出月利润与保本线",
                   "feas": "与单元经济台共用引擎，结论等于明细", "deps": "测算参数"},
                  {"nm": "系统推荐降落点", "st": "doing", "pr": "P1", "ai": "hybrid", "pg": 60,
                   "impl": "按需求密度与订单起讫聚类选点，禁飞区硬过滤，可一键采纳",
                   "feas": "已可用，采纳后进入测算", "deps": "需求网格 · 禁飞区"}]},
        "B": {"code": "B", "zh": "单元经济台", "en": "UNIT ECONOMICS", "flow": "开航② 经济性",
              "link": "../unit-econ/", "caps": [
                  {"nm": "五段测算链路", "st": "done", "pr": "P0", "ai": "hybrid", "pg": 100,
                   "impl": "订单池 → 收入 → 成本 → 盈亏 → 转正，逐段结论与明细并排",
                   "feas": "引擎已可用", "deps": "测算参数"},
                  {"nm": "成本反向倒推", "st": "doing", "pr": "P0", "ai": "hybrid", "pg": 80,
                   "impl": "由收益反推可承受租金上限，作谈判锚点，签约价回填后校准",
                   "feas": "成本单价多为假定值，按调研到谈判到实战逐步固化", "deps": "签约价回填"},
                  {"nm": "敏感性与逐年推演", "st": "doing", "pr": "P1", "ai": "hybrid", "pg": 70,
                   "impl": "租金 × 单量的利润率沙盘，五年逐年推演与按转正周期反推租金",
                   "feas": "逐年涨幅未经校准", "deps": "逐年涨幅假定"}]},
        "C": {"code": "C", "zh": "经营门户", "en": "OPS PORTAL", "flow": "决策入口",
              "link": None, "caps": [
                  {"nm": "结论三问", "st": "doing", "pr": "P0", "ai": "hybrid", "pg": 70,
                   "impl": "能不能赚 / 推进到哪 / 结论确定性，三个数由脚本现算写入",
                   "feas": "已可用，不接受手工填写", "deps": "各模块状态"},
                  {"nm": "自动播报", "st": "plan", "pr": "P1", "ai": "internal", "pg": 0,
                   "impl": "定期把停滞项与异常推给对应的人",
                   "feas": "待消息通道打通", "deps": "消息通道"}]},
        "D": {"code": "D", "zh": "商家 BD 工作台", "en": "MERCHANT CONSOLE", "flow": "开航③ 供给",
              "link": "../bd-console/", "caps": [
                  {"nm": "供给四级漏斗", "st": "doing", "pr": "P0", "ai": "hybrid", "pg": 80,
                   "impl": "商家池 → 已签 → 已开通 → 有动销，逐级归因到具体门店",
                   "feas": "已可用", "deps": "签约表每日导出"},
                  {"nm": "名单多视角", "st": "doing", "pr": "P0", "ai": "hybrid", "pg": 75,
                   "impl": "今日优先 / 签约中 / 已签 / 动销 / 全部 五个视角，加商品视角",
                   "feas": "已可用", "deps": "商品在售与上架明细"},
                  {"nm": "预警与当日建议", "st": "doing", "pr": "P1", "ai": "hybrid", "pg": 60,
                   "impl": "渠道回撤 / 零上架 / 已签零单三类异常逐家列出",
                   "feas": "已可用", "deps": "无人机渠道状态"}]},
        # ⚠ 业务助手这一条走绝对地址：站点发布根是 site/，agents/ 在它外面，
        #   写相对路径会被浏览器规范化到站点根下，点出去是 404。
        "E": {"code": "E", "zh": "业务助手", "en": "BD AGENTS", "flow": "开航③ 转述",
              "link": "https://github.com/Vanessa-0422/drone-ops-demo/blob/main/agents/README.md", "caps": [
                  {"nm": "口径单源问答", "st": "doing", "pr": "P1", "ai": "internal", "pg": 35,
                   "impl": "助手只经取数脚本拿数，不心算不外推，回复必带快照日期",
                   "feas": "结构设计已定，未接入对话平台", "deps": "对话平台"},
                  {"nm": "红线与锚点用例", "st": "doing", "pr": "P1", "ai": "internal", "pg": 30,
                   "impl": "红线管行为、锚点管数值，不过不出包",
                   "feas": "用例集已定义", "deps": "取数脚本"}]},
        "F": {"code": "F", "zh": "开航排期推演台", "en": "ROLLOUT PLANNER", "flow": "开航① 先开哪",
              "link": "../op-planner/", "caps": [
                  {"nm": "候选点位静态账", "st": "doing", "pr": "P1", "ai": "hybrid", "pg": 55,
                   "impl": "逐点位算稳态水位下的月账与保本线，出过线与不过线",
                   "feas": "已可用", "deps": "全域候选集"},
                  {"nm": "六十个月轨迹推演", "st": "doing", "pr": "P1", "ai": "hybrid", "pg": 45,
                   "impl": "覆盖与选择率两条爬坡曲线驱动逐月推演，排梯队与分城转正",
                   "feas": "曲线参数未经校准", "deps": "爬坡曲线假定"}]},
        "G": {"code": "G", "zh": "数据与口径底座", "en": "FOUNDATION", "flow": "贯穿",
              "link": None, "caps": [
                  {"nm": "口径单源与生成器", "st": "doing", "pr": "P0", "ai": "internal", "pg": 75,
                   "impl": "一份引擎文件驱动全部页面，一份生成器产出全部数据",
                   "feas": "已可用", "deps": "无"},
                  {"nm": "全链路埋点接入", "st": "block", "pr": "P0", "ai": "internal", "pg": 15,
                   "impl": "曝光到完单逐级埋点，含各环节失败与取消原因",
                   "feas": "拿不到，选择率与损失归因的前置", "deps": "上游埋点"}]},
    }

    milestones = [
        {"t": "框架与方法论", "s": "done"},
        {"t": "测算引擎与四件套", "s": "done"},
        {"t": "供给数据接入", "s": "done"},
        {"t": "单商圈盈利模型", "s": "doing"},
        {"t": "规模化复制", "s": "plan"},
    ]
    results = [
        {"k": "已就位", "s": "done", "t": "五个工具页共用一份引擎与一份数据，改口径只改一处"},
        {"k": "已就位", "s": "done", "t": "供给四级漏斗与商品覆盖逐级可见，归因到具体门店"},
        {"k": "在推", "s": "doing", "t": "单商圈盈利模型：成本单价仍是假定值，等谈判与实战回填"},
        {"k": "确定性水位", "s": "doing", "t": "驱动结论的 %d 个量里 %d 个是假定值"
            % (len(metrics), len(blocked))},
        {"k": "阻塞", "s": "block", "t": "全链路埋点未打通，配送方式选择率仍是假定值"},
    ]
    tasks = [
        {"t": "接真实配送方式选择率", "ref": "G · 底座", "go": "G",
         "d": "用无人机实单表测选择率替换假定的 5%，阻塞在上游取数。"
              "这个数一动，全部商圈的日均单量与保本缺口跟着重算。"},
        {"t": "成本单价固化", "ref": "B · 单元经济台", "go": "B",
         "d": "人力 设备 电池 场地 其他五项里，除场地租金外都是规格常量。"
              "哪些固化、哪些人工输入、谁校验，需要与业务侧逐项过一遍。"},
        {"t": "签约价回填校准", "ref": "B · 单元经济台", "go": "B",
         "d": "可承受租金上限是谈判锚点，签约价确定后要回填，"
              "否则模型永远停在反推值上，谈第二个点位时没有参照。"},
        {"t": "排期推演曲线校准", "ref": "F · 排期推演台", "go": "F",
         "d": "覆盖爬坡与选择率爬坡两条曲线的终点与周期都是拍的，"
              "先按可核区间与外推区间分段标注，不把外推段当结论用。"},
    ]
    logs = [
        [day(2), "单元经济台上线，保本线改用持续为正口径，另报首次穿越点"],
        [day(5), "商家 BD 工作台上线，供给与商品两条漏斗逐级可见"],
        [day(9), "选址沙盘接入订单起讫网格，圈选即出订单规模"],
        [day(14), "末端配送闸改逐点判，关闸按距离留存率同时减收入与成本"],
        [day(16), "全部演示数据改为脚本生成，仓库不再存任何数据文件"],
        [day(23), "确定成本反向倒推口径：不正向填租金算盈亏"],
        [day(31), "供给四级漏斗口径对齐，签约与开通分开记"],
    ]

    return {
        "snapshot": {"refreshed": day(0), "econ": day(1),
                     "launch": day(min(x["stalled_days"] for x in launch)), "supply": day(1)},
        "verdict": {
            "earn": {"v": str(worst["breakeven"] or "—"), "u": "单/天",
                     "sub": "距转正最远的是 %s：当前 %s 单/天 → 保本线 %s · 月亏 %s"
                            % (worst["nm"], worst["daily_now"], worst["breakeven"],
                               "%.1f 万" % (abs(worst["profit_now"]) / 10000))},
            "launch": {"v": str(max(x["tasks_stalled"] for x in launch)), "u": "项",
                       "sub": "%d 个商圈在推，最久一个已 %d 天没有更新"
                              % (len(launch), max(x["stalled_days"] for x in launch))},
            "certain": {"v": str(len(blocked)), "u": "项",
                        "sub": "驱动结论的 %d 个量里 %d 个仍是假定值，其中选择率影响最大"
                               % (len(metrics), len(blocked))},
        },
        "econ": econ, "launch": launch, "metrics": metrics,
        "certainty": {"fact": sum(1 for m in metrics if m["state"] == "fact"),
                      "derived": sum(1 for m in metrics if m["state"] == "derived"),
                      "assumed": len(blocked), "total": len(metrics)},
        "pillars": pillars, "modules": modules,
        "milestones": milestones, "results": results, "tasks": tasks,
        "logs": [{"date": d, "text": t} for d, t in logs],
        "rollout_head": rollout["summary"],
    }


# ── 开航排期推演 ─────────────────────────────────────────

def gen_rollout(rng):
    """全域分城市的开航排期：候选点位静态账 + 六十个月轨迹推演。

    与四商圈那条线刻意不耦合：这条线看的是全域分城市的开航顺序，
    取数范围与半径口径都与单城工具不一样，混用会得出假的口径一致。
    """
    # 四个行政区。名称与坐标均为虚构，不指向任何真实地点
    cities = [
        ("c1", "北岸市", 25.20, 55.27, 0.62, 1.00, 1.00, 15),
        ("c2", "南港市", 24.47, 54.37, 0.48, 0.74, 0.85, 12),
        ("c3", "西原市", 25.35, 55.39, 0.55, 0.52, 0.50, 10),
        ("c4", "东屿市", 25.41, 55.44, 0.51, 0.28, 0.40, 7),
    ]
    SELS = [0.20, 0.25, 0.30]     # 选择率三档，主档取中值
    MAIN = "sel25"
    COV_STEADY = 0.64             # 稳态覆盖
    names_a = ["Central", "Harbour", "Garden", "Station", "Lake", "Palm", "Gate",
               "Crescent", "Summit", "Riverside", "Old Town", "Airport", "Campus",
               "Bay", "Terrace", "Orchard", "Foundry", "Beacon"]
    names_b = ["Mall", "Plaza", "Market", "Square", "Centre", "Hub"]

    out_c, out_p = [], []
    pid = 0
    for cid, cname, clat, clng, ceil_cov, scale, rent_coef, n in cities:
        pools = []
        for i in range(n):
            pid += 1
            p = jitter_point(rng, clat, clng, rng.uniform(1.5, 14.0))
            nl = rng.randint(2, 6)
            # 点位订单池是幂律：少数几个点吃掉大半，尾巴上一串怎么算都不划算的点
            pool = int(15000 * scale * min(11.0, rng.paretovariate(1.30)))
            pools.append(pool)
            cov_now = round(rng.uniform(0.14, 0.52), 4)
            leg = round(rng.uniform(0.8, 2.4), 2)
            rent = int(6000 * rent_coef * rng.uniform(0.75, 2.2) / 100) * 100
            site = {
                "id": "s%03d" % pid, "city": cid,
                "nm": "%s %s" % (names_a[pid % len(names_a)], rng.choice(names_b)),
                "lat": p[0], "lng": p[1],
                "pool_monthly": pool, "cov_now": cov_now, "cov_ceiling": ceil_cov,
                "landings": nl, "winch": sum(1 for _ in range(nl) if rng.random() < 0.28),
                "avg_leg_km": leg,
                # 同城租金跨度接近三倍，这不是异常值，是真实商业地产的样子
                "rent_takeoff": rent,
                "planned": rng.random() < 0.12,   # 白名单：业务侧已定，不过经济性闸也要落位
            }
            # ── 静态账：稳态水位下的月账与保本线，三档选择率各算一遍 ──
            tiers = {}
            for s in SELS:
                daily = pool * COV_STEADY * s / 30
                cfg = dict(UE_DEFAULTS, **{
                    "landingPts": nl, "routeDist": int(leg * 1000),
                    "takeoffRent": rent, "landingRent": 3000 * nl,
                    "marketing": 0, "subsidyPerOrder": SUBSIDY_STEADY,
                    "salPilot": int(9200 * (1.0 if cid in ("c1", "c2") else 0.9)),
                    "salMaint": int(8600 * (1.0 if cid in ("c1", "c2") else 0.9)),
                })
                m = ue_compute(dict(cfg, dailyOrders=max(1, daily)))
                _, be = ue_breakeven(cfg, cap=900)
                tiers["sel%d" % round(s * 100)] = {
                    "daily": round(daily, 1), "profit": round(m["profit"]),
                    "breakeven": be, "pass": bool(be and daily >= be),
                    "rent_ceiling": round(m["rentCeiling"]),
                }
            site["tiers"] = tiers
            site["pass"] = tiers[MAIN]["pass"]
            out_p.append(site)
        # 英文名与中文名同为虚构，供页面切到英文时用；两者一一对应，改一个要同改另一个
        CITY_EN = {"c1": "Northbay", "c2": "Southport", "c3": "Westplain", "c4": "Eastisle"}
        out_c.append({"id": cid, "nm": cname, "nm_en": CITY_EN[cid], "lat": clat, "lng": clng,
                      "pool_monthly": int(sum(pools) / rng.uniform(0.22, 0.32)),
                      "cov_ceiling": ceil_cov, "rent_coef": rent_coef, "sites": n})

    # ── 轨迹推演：六十个月，覆盖与选择率两条爬坡曲线 ──
    COV_MONTHS, SEL_MONTHS = 24, 30
    CAP_PER_YEAR = [3, 5, 7, 8, 8]      # 逐年产能闸：一年能开几个点
    OBS_MO = 60
    today = datetime.date.today()
    base_year = today.year + 1

    def ramp(t, months, end):
        """S 形爬坡：前期慢、中段快、末期收敛。"""
        if t <= 0:
            return 0.0
        x = min(1.0, t / months)
        return end * (1 - math.exp(-3.1 * x)) / (1 - math.exp(-3.1))

    ladder = []
    for s in out_p:
        city = next(c for c in out_c if c["id"] == s["city"])
        cfg = dict(UE_DEFAULTS, **{
            "landingPts": s["landings"], "routeDist": int(s["avg_leg_km"] * 1000),
            "takeoffRent": s["rent_takeoff"], "landingRent": 3000 * s["landings"],
            "marketing": 0,
            "salPilot": int(9200 * (1.0 if s["city"] in ("c1", "c2") else 0.9)),
            "salMaint": int(8600 * (1.0 if s["city"] in ("c1", "c2") else 0.9)),
        })
        months, cum, pos_at, peak = [], 0, None, -1e18
        for t in range(1, OBS_MO + 1):
            cov = ramp(t, COV_MONTHS, s["cov_ceiling"])
            sel = ramp(t, SEL_MONTHS, SELS[1])
            daily = s["pool_monthly"] * cov * sel / 30
            # 补贴按站龄递减：开航头半年高，第一年末收敛到稳态
            sub = 10.0 if t <= 6 else max(SUBSIDY_STEADY, 10.0 - (t - 6) * 1.25)
            m = ue_compute(dict(cfg, dailyOrders=max(0.1, daily), subsidyPerOrder=round(sub, 2)))
            cum += m["profit"]
            peak = max(peak, -cum) if cum < 0 else peak
            if pos_at is None and m["profit"] >= 0:
                pos_at = t
            months.append({"t": t, "daily": round(daily, 1), "profit": round(m["profit"]),
                           "cum": round(cum)})
        tier = ("T1" if pos_at and pos_at <= 18 else
                "T2" if pos_at and pos_at <= 30 else
                "T3" if pos_at and pos_at <= 44 else
                "30+" if pos_at else "OBS")
        ladder.append({"id": s["id"], "nm": s["nm"], "city": s["city"], "cityNm": city["nm"],
                       "planned": s["planned"], "tier": tier, "pos_at": pos_at,
                       "peak_draw": round(max(0, peak)),
                       "daily_y3": months[35]["daily"], "months": months})

    # 排期：白名单先落位，其余按转正月份贪心，受逐年产能闸约束
    order = sorted(ladder, key=lambda x: (not x["planned"], x["pos_at"] or 999))
    slot, plan = {y: 0 for y in range(5)}, []
    for x in order:
        x["open_year"] = None
        x["open_q"] = None
        # 推演期内转不正的点位不进排期。白名单例外：业务侧已经定了，不过经济性闸也要落位，
        # 但它照样占一个产能名额 —— 占位不占名额会让产能闸形同虚设。
        if not x["planned"] and not x["pos_at"]:
            continue
        for y in range(5):
            if slot[y] < CAP_PER_YEAR[y]:
                slot[y] += 1
                x["open_year"] = base_year + y
                x["open_q"] = "%dQ%d" % (base_year + y, rng.randint(1, 4))
                plan.append(x["id"])
                break

    # ── 转正年：首个「当年经营净利 ≥ 0」的年 ──
    # 判据放在城市与全域这两层，与点位层的 pos_at 不是同一个数：点位层是纯配送
    # （首个月配送利润不为负的月），城市层含商业化收入。两个指标分开播报，别合并。
    # 商业化随开航点位数走：每个已开点位每月一个媒体包，售出率随站龄线性爬坡。
    MONET_PACK, MONET_SELL = 18000, [0.30, 0.80]

    def monet(t):
        s0, s1 = MONET_SELL
        return MONET_PACK * (s0 + (s1 - s0) * min(1.0, t / COV_MONTHS))

    def year_net(rows):
        """rows = [(开航年, 开航季, months)] → 逐年经营净利与当年在跑的点位数。"""
        net = {base_year + y: 0.0 for y in range(5)}
        live = {base_year + y: 0 for y in range(5)}
        for oy, oq, ms in rows:
            launch = (oy - base_year) * 12 + (int(oq[-1]) - 1) * 3 + 1
            for t, m in enumerate(ms, 1):
                cm = launch + t - 1
                if cm < 1 or cm > 60:
                    continue
                y = base_year + (cm - 1) // 12
                net[y] += m["profit"] + monet(t)
                live[y] = max(live[y], 1)
        return net, live

    def landfall_of(rows):
        net, live = year_net(rows)
        for y in sorted(net):
            # 一个点位都没开的年份，净利为零不是不亏，是还没开始
            if live[y] and net[y] >= 0:
                return y
        return None

    sched = [(x["open_year"], x["open_q"], x["months"], x["city"])
             for x in ladder if x["open_year"]]
    landfall = {c["id"]: landfall_of([r[:3] for r in sched if r[3] == c["id"]]) for c in out_c}
    landfall_all = landfall_of([r[:3] for r in sched])

    passed = sum(1 for s in out_p if s["pass"])
    tiers_n = {k: sum(1 for x in ladder if x["tier"] == k) for k in ("T1", "T2", "T3", "30+", "OBS")}
    return {
        "_note": "全部为虚构演示数据，城市与点位均不指向任何真实地点",
        "params": {"sels": SELS, "main": MAIN, "cov_steady": COV_STEADY,
                   "cov_months": COV_MONTHS, "sel_months": SEL_MONTHS,
                   "cap_per_year": CAP_PER_YEAR, "obs_months": OBS_MO,
                   "base_year": base_year, "subsidy_steady": SUBSIDY_STEADY,
                   "subsidy_launch": 10.0, "subsidy_flat_months": 6, "subsidy_step": 1.25,
                   "monet_pack": MONET_PACK, "monet_sell": MONET_SELL,
                   "ramp_k": 3.1,
                   # 几何：取餐圈 / 降落圈 / 单程航程，单位公里。全部为假定值，
                   # 推演台的工作台按它们画圈并重算池子，改了要连带重算候选点位。
                   "supply_r_km": 1.0, "land_r_km": 1.0, "range_km": 4.0},
        "cities": out_c, "sites": out_p,
        "ladder": [{k: v for k, v in x.items() if k != "months"} for x in ladder],
        "months": {x["id"]: x["months"] for x in ladder},
        "landfall": landfall, "landfall_all": landfall_all,
        "summary": {"sites": len(out_p), "cities": len(out_c),
                    "static_pass": passed, "tiers": tiers_n,
                    "landfall": landfall},
    }


# ── 商家 BD 工作台 ───────────────────────────────────────

def gen_bdboard(rng, districts, ctx, merchants, items):
    """工作台的数据源：逐商圈名单、品类矩阵、商品覆盖分档、预警、经营概览。"""
    today = datetime.date.today()
    snap = today.isoformat()

    def unit_pack(uid, ms, its, nm):
        signed = [m for m in ms if m["st"] == "signed"]
        signing = [m for m in ms if m["st"] == "signing"]
        opened = [m for m in signed if m["dOpen"] == 1]
        active = [m for m in opened if m["active"]]
        cats = sorted({m["cat"] for m in ms})
        cat_matrix = [{
            "cat": c,
            "signed": sum(1 for m in signed if m["cat"] == c),
            "signing": sum(1 for m in signing if m["cat"] == c),
            "open": sum(1 for m in ms if m["cat"] == c and m["st"] == "open"),
            "o": sum(m["o"] for m in ms if m["cat"] == c),
        } for c in cats]
        cat_matrix.sort(key=lambda x: -x["o"])
        levels = {}
        for lv in (60, 70, 80, 90, 95):
            hi = [i for i in its if i["pp"] >= lv]
            levels[str(lv)] = {
                "tot": len(hi),
                "ok": sum(1 for i in hi if i["supply"]),
                "lst": sum(1 for i in hi if i["listed"]),
            }
        top = sorted(ms, key=lambda m: -m["o"])
        acc, core_ids = 0, set()
        tot_o = sum(m["o"] for m in ms) or 1
        for m in top:
            acc += m["o"]
            core_ids.add(m["id"])
            if acc / tot_o >= 0.80:
                break
        # 配额 = 按品类结构补齐，故只在核心之外挑：核心已按贡献占掉头部，
        # 若不排除，每个品类的头部都落在核心里，配额这一档永远为空。
        quota_ids = set()
        for c in cats:
            grp = [m for m in ms if m["cat"] == c and m["id"] not in core_ids]
            grp.sort(key=lambda m: -m["o"])
            k = max(1, round(len(grp) * 0.12))
            for m in grp[:k]:
                quota_ids.add(m["id"])
        for m in ms:
            m["tier"] = "core" if m["id"] in core_ids else ("quota" if m["id"] in quota_ids else "tail")
        return {
            "nm": nm, "shops": len(ms),
            "signed": len(signed), "signing": len(signing),
            "open": len(ms) - len(signed) - len(signing),
            "opened": len(opened), "active": len(active),
            "orders": tot_o,
            "merchants": [{k: v for k, v in m.items() if k not in ("lat", "lng")} for m in ms],
            "products": [{"n": i["n"], "cat": i["cat"], "pp": i["pp"], "o": i["o"],
                          "ok": i["supply"], "listed": i["listed"],
                          "moving": i["moving"], "shop": i["shopName"]}
                         for i in sorted(its, key=lambda x: -x["o"])[:400]],
            "catMatrix": cat_matrix,
            "pcov": {"levels": levels},
            "target": {"N": max(30, int(len(ms) * 0.22)), "P": 80},
        }

    units = {}
    for d in districts:
        uid = d["id"]
        ms = [m for m in merchants if m["unit"] == uid]
        its = [i for i in items if i["unit"] == uid]
        units[uid] = unit_pack(uid, ms, its, ctx[uid]["nm"])
    units["all"] = unit_pack("all", merchants, items, "全部商圈")

    # 经营概览：逐日趋势 + 转化漏斗 + 品类结构 + 地理分布
    # 存量按商圈各自走（概览可按商圈聚焦看同一条序列），全局 = 逐日求和；
    # 新签是离散事件，由日差派生，不另存一条流量序列。末日强制对齐当前存量。
    trend = []
    uids = [d["id"] for d in districts]
    stock = {u: max(0, units[u]["signed"] - rng.randint(2, 7)) for u in uids}
    base_active = units["all"]["active"] - rng.randint(3, 9)
    for k in range(29, -1, -1):
        d = (today - datetime.timedelta(days=k)).isoformat()
        by = {}
        for u in uids:
            if k == 0:
                stock[u] = units[u]["signed"]
            elif stock[u] < units[u]["signed"] and rng.random() < 0.2:
                stock[u] += 1
            by[u] = {"sg": stock[u], "si": units[u]["signing"]}
        base_active += 1 if rng.random() < 0.22 else 0
        trend.append({"d": d,
                      "signed": sum(by[u]["sg"] for u in uids),
                      "active": min(base_active, units["all"]["active"]),
                      "orders": int(units["all"]["active"] * rng.uniform(6, 16)),
                      "by": by})
    funnel = [
        {"k": "圈内商家池", "v": units["all"]["shops"]},
        {"k": "已签", "v": units["all"]["signed"]},
        {"k": "已开通", "v": units["all"]["opened"]},
        {"k": "有动销", "v": units["all"]["active"]},
    ]
    zone = [{"id": d["id"], "nm": ctx[d["id"]]["nm"], "lat": d["hubLat"], "lng": d["hubLng"],
             "shops": units[d["id"]]["shops"], "signed": units[d["id"]]["signed"],
             "rate": round(units[d["id"]]["signed"] / max(1, units[d["id"]]["shops"]), 4)}
            for d in districts]
    cat_agg = {}
    for m in merchants:
        c = cat_agg.setdefault(m["cat"], {"cat": m["cat"], "shops": 0, "signed": 0, "o": 0})
        c["shops"] += 1
        c["o"] += m["o"]
        if m["st"] == "signed":
            c["signed"] += 1
    return {
        "snapshot": snap, "demo": True,
        "units": units,
        "order": [d["id"] for d in districts],
        "ov": {"trend": trend, "funnel": funnel, "zones": zone,
               "cats": sorted(cat_agg.values(), key=lambda x: -x["o"])},
    }


# ── 履约与转化看板 ───────────────────────────────────────

def gen_ops_board(rng, ctx):
    """单航线经营看板：六级流量漏斗 + 履约体验，只有已开航的航线有数。"""
    routes = []
    for did, c in list(ctx.items())[:2]:
        exposure = rng.randint(2600, 3800)
        funnel = [exposure]
        for rate in (rng.uniform(0.06, 0.10),     # 曝光 → 进地图，这一级最薄
                     rng.uniform(0.42, 0.66),
                     rng.uniform(0.52, 0.88),
                     rng.uniform(0.48, 0.82),
                     rng.uniform(0.52, 0.72)):
            funnel.append(int(funnel[-1] * rate))
        eta = round(rng.uniform(22, 30), 2)
        ata = round(eta + rng.uniform(4, 12), 2)
        routes.append({
            "route": c["nm"],
            "funnel": dict(zip(["曝光", "进地图", "列表页", "店内页", "下单页", "完单"], funnel)),
            "conv": [round(funnel[i + 1] / funnel[i] * 100, 2) for i in range(5)],
            "delivery": {"completed": funnel[-1], "cancelled": rng.randint(0, 2),
                         "eta_min": eta, "ata_min": ata, "gap_min": round(ata - eta, 2)},
        })
    return {"routes": routes, "benchmark_map_ctr": round(rng.uniform(11, 15), 2)}


# ── 主流程 ────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    os.makedirs(OUT, exist_ok=True)

    today = datetime.date.today()
    w_end = today - datetime.timedelta(days=1)
    w_start = w_end - datetime.timedelta(days=SCALE["window_days"] - 1)
    window = "%s–%s" % (w_start.strftime("%Y.%m.%d"), w_end.strftime("%m.%d"))

    cores = gen_cores(rng)
    districts = gen_districts(rng)
    plan_zones = gen_plan_zones(rng)
    pois = gen_pois(rng, cores)
    sectors, communities = gen_sectors(rng)
    villa = gen_villa_zones(rng)
    nfz = gen_nfz(rng)
    drone = gen_drone_candidates(rng, cores, nfz)
    demand, hourly = gen_demand_grid(rng, cores, pois)
    shops = gen_shops(rng, cores, pois)
    flows = gen_flows(rng, demand, shops)
    od = gen_od(rng, demand, shops, districts)
    merchants = gen_merchants(rng, districts, shops)
    items = gen_items(rng, merchants)
    ctx = district_context(rng, districts, od, merchants, items)

    tot_orders = sum(c[2] for c in demand)
    gmv = round(tot_orders * SCALE["city_aov"])
    mapdata = {
        "bbox": [BBOX["s"], BBOX["w"], BBOX["n"], BBOX["e"]],
        "cell": CELL, "window": window, "window_days": SCALE["window_days"],
        "stats": {"orders": tot_orders, "gmv": gmv, "aov": SCALE["city_aov"],
                  "free_pct": round(SCALE["free_delivery_share"] * 100, 1)},
        "tot_orders": tot_orders, "tot_merch": len(shops),
        "demand": demand, "shops": shops, "hourly": hourly, "flows": flows,
        "sectors": sectors, "communities": communities, "villaZones": villa,
        "nfz": nfz, "pois": pois, "drone": drone,
    }
    od["window"] = window

    rollout = gen_rollout(rng)
    ops_board = gen_ops_board(rng, ctx)
    deck = gen_deck(rng, ctx, rollout)
    deck["routes"] = ops_board["routes"]
    deck["benchmark_map_ctr"] = ops_board["benchmark_map_ctr"]
    bdboard = gen_bdboard(rng, districts, ctx, merchants, items)

    overview = {
        "window": window, "window_days": SCALE["window_days"],
        "orders_window": tot_orders, "gmv_window": gmv, "aov": SCALE["city_aov"],
        "active_merchants": len(shops),
        "poi_food": pois["counts"]["supply"],
        "poi_demand": sum(pois["counts"][k] for k in
                          ("residential", "hotel", "office", "school", "park", "villa")),
        "free_delivery_share": SCALE["free_delivery_share"],
    }

    files = {
        "overview.json": overview,
        "mapdata.json": mapdata,
        "od_matrix.json": od,
        "districts.json": {"units": ctx, "order": [d["id"] for d in districts],
                           "planZones": plan_zones, "supplyRkm": SUPPLY_RKM,
                           "fences": {d["id"]: d["fence"] for d in districts},
                           "landings": {d["id"]: d["lands"] for d in districts},
                           "window": window},
        "ue_params.json": gen_ue_params(districts, ctx),
        "board.json": deck,
        "bdboard.json": bdboard,
        "rollout.json": rollout,
    }
    for name, data in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    # 生成后自检：形态对不对，一眼看出来
    signed = sum(1 for m in merchants if m["st"] == "signed")
    opened = sum(1 for m in merchants if m["dOpen"] == 1)
    active = sum(1 for m in merchants if m["active"])
    listed = sum(1 for i in items if i["listed"])
    supply = sum(1 for i in items if i["supply"])
    print("生成完毕 · seed=%d · 窗口 %s · 输出 %s\n" % (args.seed, window, OUT))
    print("  全城：订单 %s · GMV %s · 客单价 %s · 出单商家 %s"
          % (format(tot_orders, ","), format(gmv, ","), SCALE["city_aov"], format(len(shops), ",")))
    print("  地图：需求网格 %d · 餐饮 POI %d · 需求 POI %d · 商场 %d · 禁飞面 %d"
          % (len(demand), pois["counts"]["supply"],
             overview["poi_demand"], len(pois["malls"]),
             sum(len(v) for v in nfz.values())))
    print("  起讫：起点格 %d · 明细 %s 条 · 窗口内配对单量 %s"
          % (len(od["byCell"]), format(sum(len(v) for v in od["byCell"].values()), ","),
             format(od["total"], ",")))
    print("  商圈 %d 个，起飞点 %d，降落点 %d"
          % (len(districts), len(districts), sum(len(d["lands"]) for d in districts)))
    for did, c in ctx.items():
        acc, src = servable_rate(c)
        daily = c["mo"] * acc * c["pickRate"] / 30
        _, be = ue_breakeven(unit_cfg(c))
        print("    %-6s 订单池 %8s/月 · 可承接 %5.1f%% · 日均 %6.1f · 保本线 %s"
              % (c["nm"], format(c["mo"], ","), acc * 100, daily, be or "—"))
    print("  商家漏斗：池 %d → 已签 %d (%.1f%%) → 已开通 %d (%.1f%%) → 有动销 %d (%.1f%%)"
          % (len(merchants), signed, signed / len(merchants) * 100,
             opened, opened / max(signed, 1) * 100,
             active, active / max(opened, 1) * 100))
    print("  商品漏斗：在售 %d → 已签可供给 %d (%.1f%%) → 已上架 %d (%.1f%%)"
          % (len(items), supply, supply / len(items) * 100,
             listed, listed / max(supply, 1) * 100))
    s = rollout["summary"]
    print("  开航排期：%d 城 %d 个候选点位 · 静态账过线 %d · 梯队 %s"
          % (s["cities"], s["sites"], s["static_pass"],
             " ".join("%s%d" % (k, v) for k, v in s["tiers"].items())))


if __name__ == "__main__":
    main()
