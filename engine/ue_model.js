/* ============================================================================
 * 单元经济测算引擎 —— 全站唯一口径单源
 *
 * 浏览器：window.UEModel   Node：globalThis.UEModel
 * 用法：  UEModel.compute({dailyOrders: 200, landingPts: 4, takeoffRent: 9000, ...})
 *         UEModel.findBreakeven(cfg)            → 真实保本线
 *         UEModel.rentFromPoints(rentPoints[,y]) → 逐点租金折算成标量
 *         UEModel.project(cfg[, growth])         → 五年逐年推演
 *
 * 结构要点
 *   收入 = 单均配送收入(客单价×佣金率 + 平台费 + C端配送费) × 月单量 + 广告收入
 *   成本 = 人力 + 设备折旧 + 电池 + 场地办公 + 其他 + 网络运维 + 营销补贴
 *   驱动 = 日均单量。人力与设备都是阶梯量(ceil)，所以利润对单量非单调。
 *   租金 = 逐点四模式(固定 / 仅营业额提成 / 固定+营业额提成 / 固定+配送费提成)
 *          在 rentFromPoints 独立折算后写回标量，compute 只消费标量。
 *
 * 三页共用这一份文件，由 scripts/inject_ue.py 注入到各页的
 * /*UE_MODEL_START* / … /*UE_MODEL_END* / 标记区。改公式只改这里，改完重新注入。
 *
 * 演示说明：本文件里的全部参数取值均为虚构演示值，与任何真实业务无关。
 * ==========================================================================*/
(function (g) {
  'use strict';
  var ceil = Math.ceil, min = Math.min, max = Math.max, pow = Math.pow;

  /* —— 默认参数。★ 标的是业务输入，其余是规格常量。全部为虚构演示值 —— */
  var DEFAULTS = {
    // A 单量与运营（★业务输入）
    takeoffPts: 1,            // 起飞点数量
    landingPts: 4,            // 降落点数量（有逐点租金表时 = 表行数派生）
    routeDist: 2500,          // 平均航线距离 m（单程）
    dailyOrders: 200,         // ★日均单量 = 全部成本的驱动
    peakShare: 0.12,          // 峰值小时单量占比
    lastMileLandingPts: 2,    // 送货上门降落点数（末端配送人数下限）
    lastMileShare: 0.30,      // 送货上门订单占比
    // B 收入参数（★业务输入）
    aov: 52,                  // 实付客单价
    commissionRate: 0.18,     // 商家佣金率
    platformFee: 2,           // 平台费 /单
    deliveryFee: 11,           // C端配送费 /单
    marketing: 8000,          // 广告收入 /月（计入收入与利润）
    subsidyPerOrder: 0,       // 营销补贴 /单（我方承担，调用方按视图传档）
    // C 场地成本（★唯一真正可谈的成本）
    takeoffRent: 9000,
    landingRent: 12000,
    takeoffFitoutPerPt: 3500, // 起飞点每点装修月摊
    landingFitoutPerPt: 900,  // 降落点每点装修月摊
    // 人效与调休（规格）
    annualLeaveDays: 30, weeklyOffDays: 52,   // restFactor = 365/(365-30-52)
    pilotProd: 280, maintProd: 460, groundProd: 110,
    pickupProd: null,         // 取餐人效：null = 按取餐方式取值；显式数值优先
    pickupType: 'Ride',
    pickupProdByType: { Ride: 30, Walk: 50, Other: 40 },
    lastMileProd: 32, patrolProd: 180,
    patrolMinOrders: 180,     // 日均低于此值不配末端巡游
    groundCapPerTakeoff: 4,
    // 人力月薪
    salPilot: 9200, salMaint: 8600, salPickup: 3400, salGround: 4500,
    salLastMile: 3400, salPatrol: 3400,
    // 设备配比（规格）
    droneSpeed: 10,           // m/s 巡航
    takeoffLandingMin: 4,     // 起降阶段合计分钟
    droneIdle: 0.3,           // 闲置率（充电与维护）
    maxDrones: 24,            // 单起飞点机队上限
    markerPerTakeoff: 6,
    cabinetPerOrders: 150,    // 恒温柜：每 150 日单 1 个
    cabinetMaxPerTakeoff: 4,
    chargingPerCabinet: 2,
    storagePerDrones: 2,
    // 设备月折旧单价
    depDrone: 640, depMarker: 45, depPort: 2900, depCabinet: 300,
    depCharging: 120, depStorage: 85, depSpare: 115, depBoxReturn: 44,
    // 电池（规格）
    batteryPrice: 1700, batteryCycleLife: 600, batteryPackEnergy: 390,
    elecCost: 0.41, coulombic: 0.93,
    baseConsume: 0.35, marginalConsume: 0.08, baselineDist: 1700, batterySafety: 0.75,
    // 其他成本（规格）
    equipOM: 1000, opsSupplies: 900,
    simDrone: 580, simPort: 580, simHandheld: 480,
    vehiclePerTakeoff: 600, boxPerOrder: 0.3,
    droneInsurance: 130, rtkPerTakeoff: 200,
    venueElecPerUnit: 300, broadband: 1000, networkOM: 1000,
    daysPerMonth: 30,         // 月度视图 30；五年视图 project 内用 365/12

    /* 地面协同人力：取餐 + 末端配送 + 末端巡游。
       总开关 groundStaff 管取餐员（没有它餐到不了起飞点，不是划不划算的选项），
       末端两项另受 ROI 闸 lastMileSwitch 管，逐单量判。 */
    groundStaff: true,
    roiMultiple: 2,           // 末端 ROI 阈值倍数（假定值，待校准）
    lastMileSwitch: 'auto',   // auto 按 ROI 逐单量判 / on 恒计入 / off 恒不计
    equipDepOn: true,

    /* 逐点末端闸。不传 landingPools 时整段不启用，退回商圈级单闸。
         landingPools: [{ share, bands:[≤1km 占比, 1–2km 占比] }, …]
       归属口径 = 最近点：一单只归离它最近且在圈内的那个降落点，不重复计。 */
    landingPools: null,

    /* 关闸的标价：某个降落点关掉末端配送后只能柜取，该点上门订单按距离的留存率。
       这不是绝对自提意愿，是改变履约形态时的相对调整 —— 因为配送方式选择率的口径
       是「在大网配送与无人机配送之间选无人机的概率」，本身混合了柜取与上门两种形态。
       只取两档：≤1km 视为不丢单，1–2km 取 0.5，超出本就在降落圈外。 */
    lmKeepBands: [{ maxKm: 1.0, keep: 1.00 }, { maxKm: 2.0, keep: 0.50 }]
  };

  /* 营销补贴分档（假定值）。取值随站龄走，调用方按视图取档，不要在引擎里写死一个数。 */
  var SUBSIDY_TIERS = { steady: 2.5, firstYear: 7.5, firstHalf: 10 };

  /* 商圈上下文覆盖：起飞点就在商场里，取餐是楼内步行，人效远高于跑单口径。 */
  var DISTRICT_OVERRIDES = { pickupProd: 80, peakShare: 0.12 };

  function compute(o) {
    var p = Object.assign({}, DEFAULTS, o || {});
    var rest = 365 / (365 - p.annualLeaveDays - p.weeklyOffDays);
    var monthlyOrders = p.dailyOrders * p.daysPerMonth;
    var peakHourVol = p.dailyOrders * p.peakShare;
    var lastMileDaily = p.dailyOrders * p.lastMileShare;

    /* 单均收入前移：末端 ROI 闸在人力段就要用它。 */
    var merchantCommission = p.aov * p.commissionRate + p.platformFee;
    var revPerOrder = merchantCommission + p.deliveryFee;
    /* 净单均 = 毛单均 − 我方承担的补贴。末端 ROI 闸的分子只许用它：
       闸问的是多派这一班末端人力撬动的订单值不值这份人力，而订单拖进来的补贴是
       逐单流出，不管报表上它坐在收入减项还是成本行。分子分母必须同口径。 */
    var revPerOrderNet = revPerOrder - p.subsidyPerOrder;
    var pickupProd = (p.pickupProd != null) ? p.pickupProd
      : (p.pickupProdByType[p.pickupType] != null ? p.pickupProdByType[p.pickupType] : p.pickupProdByType.Other);

    /* —— 逐点末端闸 ——
       ① 每个降落点各判各的 ROI，分子分母都只算该点的上门订单与末端人力；
       ② 关闸的点只能柜取，该点上门订单按 lmKeepBands 留存率打折，收入与成本一起减。
       必须同时减收入：只删成本不删收入的话，关一个点永远是净赚，保本线只会降，
       那不是经济性变好，是模型少算一笔账。
       ROI 用该点未打折的需求算 —— 问的是在这里开末端值不值，开着就不丢单。 */
    var lmPts = null, demandDaily = p.dailyOrders;
    if (Array.isArray(p.landingPools) && p.landingPools.length) {
      var shTot = 0, i;
      for (i = 0; i < p.landingPools.length; i++) shTot += (+p.landingPools[i].share || 0);
      var even = !(shTot > 0);
      lmPts = p.landingPools.map(function (q, idx) {
        var sh = even ? 1 / p.landingPools.length : (+q.share || 0) / shTot;
        var dI = demandDaily * sh;
        var lmDI = dI * p.lastMileShare;
        // 逐点人数下限 = 1。商圈级的 lastMileLandingPts 在这一档不参与，
        // 否则低单量下每个点都 2 人起，ROI 被除散，逐点化会退化成全关。
        var staff = max(1, ceil(lmDI / p.lastMileProd)) * rest;
        var costI = staff * p.salLastMile;
        var leverI = dI * p.daysPerMonth * p.lastMileShare * revPerOrderNet;
        var roiI = costI > 0 ? leverI / costI : Infinity;
        var onI = !!p.groundStaff && (p.lastMileSwitch === 'on' ? true
          : p.lastMileSwitch === 'off' ? false : roiI >= p.roiMultiple);
        var bd = q.bands || [1], bsum = 0, keep = 0, k;
        for (k = 0; k < p.lmKeepBands.length; k++) bsum += (+bd[k] || 0);
        if (bsum <= 0) keep = p.lmKeepBands[0].keep;
        else for (k = 0; k < p.lmKeepBands.length; k++)
          keep += ((+bd[k] || 0) / bsum) * p.lmKeepBands[k].keep;
        return { i: idx, share: sh, daily: dI, lmDaily: lmDI, staff: staff, cost: costI,
                 leverRev: leverI, roi: roiI, on: onI, keep: keep,
                 lostDaily: onI ? 0 : lmDI * (1 - keep) };
      });
      var lost = 0;
      for (i = 0; i < lmPts.length; i++) lost += lmPts[i].lostDaily;
      p.dailyOrders = demandDaily - lost;
      monthlyOrders = p.dailyOrders * p.daysPerMonth;
      peakHourVol = p.dailyOrders * p.peakShare;
      lastMileDaily = p.dailyOrders * p.lastMileShare;
    }

    // —— 机队规模 ——
    var cycleMin = p.routeDist * 2 / p.droneSpeed / 60 + p.takeoffLandingMin;
    var tripsPerHr = 60 / cycleMin;
    var peakDrones = peakHourVol > 0 ? ceil(peakHourVol / tripsPerHr) : 0;
    var fleetLimit = p.maxDrones * p.takeoffPts;   // 上限随起飞点数放大
    var drones = min(ceil(peakDrones * (1 + p.droneIdle)), fleetLimit);

    // —— 人力（阶梯用工 × 调休系数）——
    var nPilot = ceil(p.dailyOrders / p.pilotProd) * rest;
    var nMaint = ceil(p.dailyOrders / p.maintProd) * rest;
    var nPickup = ceil(p.dailyOrders / pickupProd) * rest;
    var nGround = min(ceil(p.dailyOrders / p.groundProd), p.groundCapPerTakeoff * p.takeoffPts) * rest;
    var lmOpenN = 0, lmOpenDaily = 0, z;
    if (lmPts) for (z = 0; z < lmPts.length; z++) if (lmPts[z].on) {
      lmOpenN++; lmOpenDaily += lmPts[z].lmDaily;
    }
    var nLastMile = lmPts
      ? (lmOpenN ? max(lmOpenN, ceil(lmOpenDaily / p.lastMileProd)) * rest : 0)
      : max(p.lastMileLandingPts, ceil(lastMileDaily / p.lastMileProd)) * rest;
    var nPatrol = p.dailyOrders < p.patrolMinOrders ? 0 : ceil(p.dailyOrders / p.patrolProd) * rest;

    var cPilot = nPilot * p.salPilot, cMaint = nMaint * p.salMaint, cGround = nGround * p.salGround;
    /* 三项地面人力的原值始终算出（供 ROI 判决），计入 labor 分两层门控：
         取餐员   只随总开关 groundStaff
         末端两项 受 ROI 闸，分母只算这两项，与分子（上门订单收入）同口径，逐单量判
       分母若混进取餐员，ROI 会恒在 1 以下、闸恒关，取餐员被一起剔除，
       等于模型认为餐是自己飞到起飞点的。 */
    var cPickupRaw = nPickup * p.salPickup,
        cLastMileRaw = nLastMile * p.salLastMile,
        cPatrolRaw = nPatrol * p.salPatrol;
    var gsOn = p.groundStaff ? 1 : 0;
    var cPickup = cPickupRaw * gsOn;

    /* 逐点档下 nLastMile 只含开闸点的人力，全关时是 0，直接拿它当分母商圈级 ROI 会变 Infinity。
       所以逐点档的商圈级 ROI 改成把每个点都当作开着的合计，语义仍是
       「在这个商圈铺末端配送划不划算」，且恒有定义。逐点的真实判决在 lmPts[].on 上。 */
    var lmCost, lmLeverRev;
    if (lmPts) {
      var cAll = 0, rAll = 0;
      for (z = 0; z < lmPts.length; z++) { cAll += lmPts[z].cost; rAll += lmPts[z].leverRev; }
      lmCost = cAll + cPatrolRaw;
      lmLeverRev = rAll;
    } else {
      lmCost = cLastMileRaw + cPatrolRaw;
      lmLeverRev = monthlyOrders * p.lastMileShare * revPerOrderNet;
    }
    var lmRoi = lmCost > 0 ? lmLeverRev / lmCost : Infinity;
    var lmAdvise = lmRoi >= p.roiMultiple;
    var lmOn = lmPts ? (gsOn && lmOpenN > 0 ? 1 : 0)
      : gsOn && (p.lastMileSwitch === 'on' ? 1
        : p.lastMileSwitch === 'off' ? 0 : (lmAdvise ? 1 : 0));
    var cLastMile = cLastMileRaw * lmOn, cPatrol = cPatrolRaw * lmOn;
    var staffCostAll = cPickupRaw + cLastMileRaw + cPatrolRaw;
    var labor = cPilot + cMaint + cPickup + cGround + cLastMile + cPatrol;

    // —— 设备折旧 ——
    var nMarker = p.takeoffPts * p.markerPerTakeoff;
    var nPort = p.landingPts;
    var nCabinet = min(ceil(p.dailyOrders / p.cabinetPerOrders), p.cabinetMaxPerTakeoff * p.takeoffPts);
    var nCharging = nCabinet * p.chargingPerCabinet;
    var nStorage = ceil(drones / p.storagePerDrones);
    var nSpare = p.takeoffPts;
    var nBoxReturn = p.landingPts;
    var equipDepRaw = drones * p.depDrone + nMarker * p.depMarker + nPort * p.depPort
      + nCabinet * p.depCabinet + nCharging * p.depCharging + nStorage * p.depStorage
      + nSpare * p.depSpare + nBoxReturn * p.depBoxReturn;
    var equipDep = p.equipDepOn ? equipDepRaw : 0;

    // —— 电池（随航程）——
    var consumeRatio = p.baseConsume + (p.routeDist / 1000 - p.baselineDist / 1000) * p.marginalConsume;
    var batHwPerOrder = consumeRatio / p.batterySafety * (p.batteryPrice / p.batteryCycleLife);
    var batElecPerOrder = consumeRatio * p.batteryPackEnergy / p.coulombic * p.elecCost / 1000;
    var batteryHw = batHwPerOrder * monthlyOrders;
    var batteryElec = batElecPerOrder * monthlyOrders;
    var battery = batteryHw + batteryElec;

    // —— 场地与办公 ——
    var rent = p.takeoffRent + p.landingRent;
    var takeoffFitoutM = (p.takeoffFitoutM != null) ? p.takeoffFitoutM : p.takeoffPts * p.takeoffFitoutPerPt;
    var landingFitoutM = (p.landingFitoutM != null) ? p.landingFitoutM : p.landingPts * p.landingFitoutPerPt;
    var fitout = takeoffFitoutM + landingFitoutM;
    var venueElec = p.venueElecPerUnit * (p.landingPts + p.takeoffPts);
    var venueOffice = rent + fitout + venueElec + p.broadband;

    // —— 其他成本 ——
    var nHandheld = (p.handheldCount != null) ? p.handheldCount : ceil(nGround + nPickup);
    var simDroneC = ceil(drones) * p.simDrone,
        simPortC = ceil(nPort) * p.simPort,
        simHandheldC = nHandheld * p.simHandheld;
    var simTotal = simDroneC + simPortC + simHandheldC;
    var vehicle = p.takeoffPts * p.vehiclePerTakeoff;
    var box = monthlyOrders * p.boxPerOrder;
    var insurance = drones * p.droneInsurance;
    var rtk = p.takeoffPts * p.rtkPerTakeoff;
    var other = p.equipOM * p.takeoffPts + p.opsSupplies * p.takeoffPts
      + simTotal + vehicle + box + insurance + rtk;

    /* 营销补贴单列成科目，不并进「其他」：它是当前最大的一笔逐单流出，
       并进去以后在成本结构里就看不见了。 */
    var subsidy = p.subsidyPerOrder * monthlyOrders;
    var deliveryCost = labor + equipDep + battery + venueOffice + other + p.networkOM + subsidy;

    // —— 收入 ——
    var revDelivery = revPerOrder * monthlyOrders;
    var revenue = revDelivery + p.marketing;

    // —— 利润 ——
    var profit = revenue - deliveryCost;
    var margin = revenue ? profit / revenue : 0;
    var costPerOrder = monthlyOrders ? deliveryCost / monthlyOrders : 0;
    var profitPerOrder = revPerOrder - costPerOrder;

    /* 局部估计的平衡点：可变成本只算电池与餐箱，其余视为固定。
       远低于平衡点时会严重低估，真实保本走 findBreakeven。 */
    var varCostPerOrder = monthlyOrders > 0 ? (battery + box) / monthlyOrders : 0;
    var contributionPerOrder = revPerOrder - varCostPerOrder;
    var breakEvenMonthly = contributionPerOrder > 0 ? deliveryCost / contributionPerOrder : Infinity;
    var breakEvenDaily = breakEvenMonthly / p.daysPerMonth;

    /* 成本反向倒推：给定单量、其余成本已知时，仍能保本的最大可承受月租金。
       这是谈判锚点 —— 不正向填租金算盈亏，而由收益反推能承受多少。 */
    var nonRentCost = deliveryCost - rent;
    var rentCeiling = revenue - nonRentCost;
    var negotiationRoom = rentCeiling - rent;

    return {
      inputs: p, restFactor: rest,
      monthlyOrders: monthlyOrders, dailyOrders: p.dailyOrders,
      peakHourVol: peakHourVol, drones: drones, peakDrones: peakDrones,
      revPerOrder: revPerOrder, revPerOrderNet: revPerOrderNet,
      merchantCommission: merchantCommission,
      revDelivery: revDelivery, marketing: p.marketing, revenue: revenue,
      labor: labor, equipDep: equipDep, battery: battery, venueOffice: venueOffice,
      other: other, networkOM: p.networkOM, subsidy: subsidy,
      subsidyPerOrder: p.subsidyPerOrder, deliveryCost: deliveryCost,
      rent: rent, nonRentCost: nonRentCost,
      profit: profit, margin: margin, costPerOrder: costPerOrder, profitPerOrder: profitPerOrder,
      varCostPerOrder: varCostPerOrder, breakEvenMonthly: breakEvenMonthly,
      breakEvenDaily: breakEvenDaily,
      rentCeiling: rentCeiling, negotiationRoom: negotiationRoom,
      /* 这是读数不是入参。任何一个字段都不许回填进 cfg 再喂给跨单量的扫描与推演 ——
         那会把某一个单量上的判决冻结着用到整条扫描上。
         要 ROI 的分母就用 lmCost（末端两项），要三项总额就用 costAll，两者别混算。 */
      groundStaffEval: {
        on: !!p.groundStaff, costAll: staffCostAll, lmCost: lmCost, leverRev: lmLeverRev,
        roi: lmRoi, advise: lmAdvise, lmOn: !!lmOn, pickupOn: !!gsOn,
        perPoint: !!lmPts, openPts: lmPts ? lmOpenN : null, points: lmPts,
        demandDaily: demandDaily, effDaily: p.dailyOrders,
        lostDaily: lmPts ? demandDaily - p.dailyOrders : 0
      },
      breakdown: {
        labor: { total: labor, items: [
          ['飞手', nPilot, p.salPilot, cPilot], ['机务', nMaint, p.salMaint, cMaint],
          ['取餐员', nPickup, p.salPickup, cPickup], ['地勤', nGround, p.salGround, cGround],
          ['末端配送', nLastMile, p.salLastMile, cLastMile], ['末端巡游', nPatrol, p.salPatrol, cPatrol]
        ] },
        equipDep: { total: equipDep, items: [
          ['无人机 A1', drones, p.depDrone], ['地面标识', nMarker, p.depMarker],
          ['降落柜', nPort, p.depPort], ['室外恒温柜', nCabinet, p.depCabinet],
          ['充电柜', nCharging, p.depCharging], ['机库', nStorage, p.depStorage],
          ['备品柜', nSpare, p.depSpare], ['餐箱回收柜', nBoxReturn, p.depBoxReturn]
        ] },
        battery: { total: battery, hardware: batteryHw, electricity: batteryElec, consumeRatio: consumeRatio },
        venueOffice: { total: venueOffice, rent: rent, takeoffRent: p.takeoffRent, landingRent: p.landingRent,
          fitout: fitout, takeoffFitout: takeoffFitoutM, landingFitout: landingFitoutM,
          venueElec: venueElec, broadband: p.broadband },
        other: { total: other, sim: simTotal, simDrone: simDroneC, simPort: simPortC, simHandheld: simHandheldC,
          vehicle: vehicle, box: box, insurance: insurance, rtk: rtk,
          equipOM: p.equipOM * p.takeoffPts, opsSupplies: p.opsSupplies * p.takeoffPts }
      }
    };
  }

  /* ==========================================================================
   * 逐点租金层 —— 独立于 compute 的纯函数。
   * UI 维护 rentPoints → rentFromPoints 折算 → 写回四个标量 → compute 与全部
   * 既有消费方（热力图 / 反推 / URL 载荷）零改动。
   * RentPoint = { name, mode, fixed, pct, growthRent, fitoutTotal, amortMonths,
   *               dailyOrders, aov, cfee, growthOrders, growthAov, growthCfee }
   * ========================================================================*/
  var RENT_MODES = ['fixed', 'pct_gmv', 'fixed_pct_gmv', 'fixed_pct_cfee'];
  function modeHasFixed(m) { return m === 'fixed' || m === 'fixed_pct_gmv' || m === 'fixed_pct_cfee'; }
  function modeHasGmv(m) { return m === 'pct_gmv' || m === 'fixed_pct_gmv'; }
  function modeHasCfee(m) { return m === 'fixed_pct_cfee'; }

  function pointMonthly(pt, yearIdx) {
    var y = yearIdx || 1;
    var fixedM = modeHasFixed(pt.mode) ? (pt.fixed || 0) * pow(1 + (pt.growthRent || 0), y - 1) : 0;
    var d = (pt.dailyOrders || 0) * pow(1 + (pt.growthOrders || 0), y - 1);
    var aov = (pt.aov || 0) * pow(1 + (pt.growthAov || 0), y - 1);
    var cf = (pt.cfee || 0) * pow(1 + (pt.growthCfee || 0), y - 1);
    var base = (modeHasGmv(pt.mode) ? d * aov : 0) + (modeHasCfee(pt.mode) ? d * cf : 0);
    return fixedM + base * (pt.pct || 0) * 365 / 12;
  }
  function pointFitoutM(pt) {
    var mo = pt.amortMonths || 12;
    return mo > 0 ? (pt.fitoutTotal || 0) / mo : 0;
  }
  function rentFromPoints(rp, yearIdx) {
    var to = (rp && rp.takeoff) || [], la = (rp && rp.landing) || [];
    var r = { takeoffRent: 0, landingRent: 0, takeoffFitoutM: 0, landingFitoutM: 0,
              takeoffPts: to.length || 1, landingPts: la.length };
    to.forEach(function (pt) { r.takeoffRent += pointMonthly(pt, yearIdx); r.takeoffFitoutM += pointFitoutM(pt); });
    la.forEach(function (pt) { r.landingRent += pointMonthly(pt, yearIdx); r.landingFitoutM += pointFitoutM(pt); });
    return r;
  }
  /* 商圈快照 → 默认逐点表（全固定、均摊）。
     恒等式：rentFromPoints(defaultRentPoints(u)) ≡ 快照里的四个标量。 */
  function defaultRentPoints(u, fitoutT, fitoutL) {
    var lands = u.lands || 0, names = u.landNames || [];
    var per = lands ? +((u.landingRent || 0) / lands).toFixed(2) : 0;
    var arr = [];
    for (var i = 0; i < lands; i++) arr.push({
      name: names[i] || null, nameKey: '降落点', nameIdx: i + 1,
      mode: 'fixed', fixed: per, pct: 0, growthRent: 0,
      fitoutTotal: fitoutL != null ? fitoutL : 10800, amortMonths: 12,
      dailyOrders: null, aov: null, cfee: null, growthOrders: 0, growthAov: 0, growthCfee: 0
    });
    return {
      takeoff: [{
        name: u.hubName || null, nameKey: '起飞点', nameIdx: null,
        mode: 'fixed', fixed: (u.takeoffRent != null ? u.takeoffRent : DEFAULTS.takeoffRent),
        pct: 0, growthRent: 0,
        fitoutTotal: fitoutT != null ? fitoutT : 42000, amortMonths: 12,
        dailyOrders: null, aov: null, cfee: null, growthOrders: 0, growthAov: 0, growthCfee: 0
      }],
      landing: arr
    };
  }

  /* ==========================================================================
   * 五年逐年推演
   *   年 = 日均 × 365（daysPerMonth 取 365/12）；设备逐年重算；折旧五年不到期；
   *   装修计入首年一次性（月度视图仍按 amortMonths 摊，两个口径并存）；
   *   租金随逐点表逐年推演（固定部分复利，提成部分随该年营业额或配送费）。
   * ========================================================================*/
  var GROWTH_DEF = {
    orders: [0, 0.12, 0.12, 0.10, 0.10],
    salary: 0.03, aov: 0.03, marketing: 0.10,
    commission: 0, platformFee: 0, cfee: 0
  };
  function project(o, gr) {
    var base = Object.assign({}, o || {});
    var G = Object.assign({}, GROWTH_DEF, gr || {});
    var orders = G.orders && G.orders.length ? G.orders : GROWTH_DEF.orders;
    var d0 = (base.dailyOrders != null) ? base.dailyOrders : DEFAULTS.dailyOrders;
    var rp = base.rentPoints || null;
    var fitout1 = 0;
    if (rp) {
      (rp.takeoff || []).concat(rp.landing || []).forEach(function (pt) { fitout1 += pt.fitoutTotal || 0; });
    } else {
      var tfM = (base.takeoffFitoutM != null) ? base.takeoffFitoutM
        : ((base.takeoffPts != null ? base.takeoffPts : DEFAULTS.takeoffPts) * DEFAULTS.takeoffFitoutPerPt);
      var lfM = (base.landingFitoutM != null) ? base.landingFitoutM
        : ((base.landingPts != null ? base.landingPts : DEFAULTS.landingPts) * DEFAULTS.landingFitoutPerPt);
      fitout1 = (tfM + lfM) * 12;
    }
    var pick = function (k) { return (base[k] != null) ? base[k] : DEFAULTS[k]; };
    var aov0 = pick('aov'), mkt0 = pick('marketing'), cr0 = pick('commissionRate'),
        pf0 = pick('platformFee'), df0 = pick('deliveryFee');
    var salKeys = ['salPilot', 'salMaint', 'salPickup', 'salGround', 'salLastMile', 'salPatrol'];
    var sal0 = {}; salKeys.forEach(function (k) { sal0[k] = pick(k); });

    var years = [], landYear = null, daily = d0, totRev = 0, totCost = 0, totProfit = 0;
    for (var t = 1; t <= 5; t++) {
      if (t > 1) daily = daily * (1 + (orders[t - 1] || 0));
      var f = pow(1 + G.salary, t - 1);
      var cfgY = Object.assign({}, base, {
        rentPoints: undefined,
        dailyOrders: daily,
        daysPerMonth: 365 / 12,
        aov: aov0 * pow(1 + G.aov, t - 1),
        marketing: mkt0 * pow(1 + G.marketing, t - 1),
        commissionRate: cr0 * pow(1 + G.commission, t - 1),
        platformFee: pf0 * pow(1 + G.platformFee, t - 1),
        deliveryFee: df0 * pow(1 + G.cfee, t - 1),
        equipDepOn: true,
        takeoffFitoutM: 0, landingFitoutM: 0
      });
      salKeys.forEach(function (k) { cfgY[k] = sal0[k] * f; });
      if (rp) {
        var r = rentFromPoints(rp, t);
        cfgY.takeoffRent = r.takeoffRent; cfgY.landingRent = r.landingRent;
        if (o == null || o.takeoffPts == null) cfgY.takeoffPts = r.takeoffPts;
        if (o == null || o.landingPts == null) cfgY.landingPts = r.landingPts;
      }
      var m = compute(cfgY);
      var rev = m.revenue * 12;
      var cost = m.deliveryCost * 12 + (t === 1 ? fitout1 : 0);
      var pr = rev - cost;
      if (landYear == null && pr >= 0) landYear = t;
      totRev += rev; totCost += cost; totProfit += pr;
      years.push({ t: t, daily: daily, annualOrders: daily * 365, monthlyOrders: m.monthlyOrders,
        revenue: rev, cost: cost, profit: pr, margin: rev ? pr / rev : 0, rent: m.rent * 12, m: m });
    }
    return { years: years, landYear: landYear,
      totals: { revenue: totRev, cost: totCost, profit: totProfit },
      fitoutOneOff: fitout1, growth: G };
  }

  /* ==========================================================================
   * 真实保本线
   *   compute().breakEvenDaily 是局部估计（成本冻结在当前单量），远低于平衡点时严重低估。
   *   这里把成本随单量的阶梯上升也算进去，逐单量扫描。
   *
   *   判据 = 持续为正，取最后一段连续为正的起点，不取首次穿越：
   *   利润对单量非单调（人力是 ceil 阶梯、收入是线性，每上一级台阶利润掉一次），
   *   首次穿零的那个单量未必稳得住。
   *   因此必须扫满 CAP，不许一穿越就 return，也不许用二分 ——
   *   非单调下二分会落进某个孤立的正区间里。
   * ========================================================================*/
  function findBreakeven(o) {
    var cfg = o || {};
    var dpm = cfg.daysPerMonth || DEFAULTS.daysPerMonth;
    var CAP = 3000;
    /* 机队运力上限：只报不拦。封顶之后引擎还在按单量加收入、不再加飞机，
       成本摊薄让保本线仍然解得出来，但那个单量这套机队飞不了。
       判据用真实运力（封顶后在飞架数 × 每架每小时趟数 ÷ 峰值占比），
       不是「飞机数撞顶」那一刻 —— 撞顶只是成本停涨点。 */
    var p0 = Object.assign({}, DEFAULTS, cfg);
    var cyc = p0.routeDist * 2 / p0.droneSpeed / 60 + p0.takeoffLandingMin;
    var tph = 60 / cyc;
    var flying = (p0.maxDrones * p0.takeoffPts) / (1 + p0.droneIdle);
    var fleetCapDaily = p0.peakShare > 0 ? Math.floor(flying * tph / p0.peakShare) : null;

    var stable = null, firstCross = null;
    for (var d = 1; d <= CAP; d++) {
      if (compute(Object.assign({}, cfg, { dailyOrders: d })).profit >= 0) {
        if (firstCross === null) firstCross = d;
        if (stable === null) stable = d;
      } else {
        stable = null;
      }
    }
    if (stable === null) {
      return { daily: Infinity, monthly: Infinity, feasible: false,
               firstCross: firstCross, dipAfterFirst: firstCross !== null,
               fleetCapDaily: fleetCapDaily, overFleet: false };
    }
    return { daily: stable, monthly: stable * dpm, feasible: true,
             firstCross: firstCross, dipAfterFirst: firstCross !== null && firstCross !== stable,
             fleetCapDaily: fleetCapDaily,
             overFleet: fleetCapDaily != null && stable > fleetCapDaily };
  }

  var API = {
    DEFAULTS: DEFAULTS, GROWTH_DEF: GROWTH_DEF, SUBSIDY_TIERS: SUBSIDY_TIERS,
    DISTRICT_OVERRIDES: DISTRICT_OVERRIDES, RENT_MODES: RENT_MODES,
    compute: compute, findBreakeven: findBreakeven,
    rentFromPoints: rentFromPoints, defaultRentPoints: defaultRentPoints,
    pointMonthly: pointMonthly, project: project
  };
  g.UEModel = API;
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
})(typeof globalThis !== 'undefined' ? globalThis : this);
