const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, TableOfContents,
  LevelFormat
} = require("docx");

// ─── Constants ──────────────────────────────────────────
const FONT = "Microsoft YaHei";
const FONT_MONO = "Consolas";
const BLUE = "2E75B6";
const LIGHT_BLUE = "D5E8F0";
const GRAY_BG = "F2F2F2";
const PAGE_W = 11906; // A4
const PAGE_H = 16838;
const MARGIN = 1440;
const CONTENT_W = PAGE_W - 2 * MARGIN; // 9026

// ─── Helper: border ─────────────────────────────────────
const thinBorder = { style: BorderStyle.SINGLE, size: 1, color: "BFBFBF" };
const cellBorders = { top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

// ─── Helper: create a text paragraph ────────────────────
function p(text, opts = {}) {
  const { bold, spacing, align, indent, font, size, color } = opts;
  return new Paragraph({
    alignment: align || AlignmentType.JUSTIFIED,
    spacing: spacing || { before: 80, after: 80, line: 360 },
    indent: indent,
    children: [
      new TextRun({
        text, font: font || FONT, size: size || 21,
        bold: bold || false, color: color,
      }),
    ],
  });
}

// ─── Helper: heading ────────────────────────────────────
function h1(text) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: FONT, bold: true, size: 32, color: BLUE })] }); }
function h2(text) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text, font: FONT, bold: true, size: 26, color: BLUE })] }); }

// ─── Helper: code block ─────────────────────────────────
function codeBlock(lines) {
  return lines.map(line => new Paragraph({
    spacing: { before: 0, after: 0, line: 276 },
    shading: { fill: GRAY_BG, type: ShadingType.CLEAR },
    indent: { left: 360 },
    children: [new TextRun({ text: line, font: FONT_MONO, size: 16, color: "333333" })],
  }));
}

// ─── Helper: blockquote ─────────────────────────────────
function blockquote(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 100, after: 100, line: 340 },
    indent: { left: 360 },
    shading: { fill: "F0F5FA", type: ShadingType.CLEAR },
    children: [
      new TextRun({ text, font: FONT, size: opts.size || 20, bold: opts.bold, color: "444444" }),
    ],
  });
}

// ─── Helper: horizontal rule ────────────────────────────
function hr() {
  return new Paragraph({
    spacing: { before: 200, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC", space: 1 } },
    children: [],
  });
}

// ─── Helper: table ──────────────────────────────────────
function makeTable(headers, rows, colWidths) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders: cellBorders,
      width: { size: colWidths[i], type: WidthType.DXA },
      shading: { fill: BLUE, type: ShadingType.CLEAR },
      margins: cellMargins,
      verticalAlign: "center",
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: h, font: FONT, size: 20, bold: true, color: "FFFFFF" })],
      })],
    })),
  });

  const dataRows = rows.map(row => new TableRow({
    children: row.map((cell, i) => {
      const isMulti = Array.isArray(cell);
      const texts = isMulti ? cell : [cell];
      return new TableCell({
        borders: cellBorders,
        width: { size: colWidths[i], type: WidthType.DXA },
        margins: cellMargins,
        children: texts.map(t => new Paragraph({
          spacing: { before: 20, after: 20, line: 300 },
          children: [new TextRun({ text: t, font: FONT, size: 19 })],
        })),
      });
    }),
  }));

  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...dataRows],
  });
}

// ─── Helper: simple two-line header table ───────────────
function metaTable(rows) {
  const totalW = CONTENT_W;
  const cw1 = 1200;
  const cw2 = CONTENT_W - 1200;
  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: [cw1, cw2],
    rows: rows.map(([k, v]) => new TableRow({
      children: [
        new TableCell({
          borders: cellBorders,
          width: { size: cw1, type: WidthType.DXA },
          shading: { fill: LIGHT_BLUE, type: ShadingType.CLEAR },
          margins: cellMargins,
          children: [new Paragraph({ children: [new TextRun({ text: k, font: FONT, size: 20, bold: true, color: BLUE })] })],
        }),
        new TableCell({
          borders: cellBorders,
          width: { size: cw2, type: WidthType.DXA },
          margins: cellMargins,
          children: [new Paragraph({ children: [new TextRun({ text: v, font: FONT, size: 20 })] })],
        }),
      ],
    })),
  });
}

// ─── Helper: bullet list ────────────────────────────────
function bullet(text, opts = {}) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40, line: 340 },
    children: [new TextRun({ text, font: FONT, size: 21, ...opts })],
  });
}

// ======================================================================
// DOCUMENT CONTENT
// ======================================================================

const children = [];

// ─── Cover / Title Block ────────────────────────────────
children.push(new Paragraph({ spacing: { before: 2400 } }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  children: [new TextRun({ text: "MetaTrader 4/5", font: FONT, size: 44, bold: true, color: BLUE })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  children: [new TextRun({ text: "账户跟单同步系统", font: FONT, size: 44, bold: true, color: BLUE })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 400 },
  children: [new TextRun({ text: "技术可行性报告（脱敏版）", font: FONT, size: 36, bold: true, color: "333333" })],
}));
children.push(hr());
children.push(new Paragraph({ spacing: { before: 300 } }));
children.push(metaTable([
  ["密  级", "对外分享"],
  ["日  期", "2026-06-18"],
  ["版  本", "v1.0"],
]));
children.push(new Paragraph({ spacing: { before: 200 } }));
children.push(blockquote("说明：本报告为对外分享版本，核心技术参数和实施细节已做知识保护处理。如需完整技术方案，请联系项目负责人。", { bold: true }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ─── Table of Contents ─────────────────────────────────
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 200, after: 400 },
  children: [new TextRun({ text: "目  录", font: FONT, size: 36, bold: true, color: BLUE })],
}));
children.push(new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ======================================================================
// CHAPTER 1
// ======================================================================
children.push(h1("1. 项目概述"));
children.push(h2("1.1 项目背景"));
children.push(p("在 MetaTrader 4/5 交易环境中，存在以下需求：一个已在运行交易策略的账户（主账户），需要将其交易操作同步复制到其他账户（从账户），并且支持未来扩展为一对多的跟单架构。"));

children.push(h2("1.2 项目目标"));
children.push(makeTable(
  ["目标", "描述"],
  [
    ["主要目标", "实现主账户到从账户的交易操作实时跟单"],
    ["扩展目标", "支持一对多跟单架构"],
    ["性能目标", "端到端延迟控制在行业优秀水平内"],
    ["可靠性目标", "具备生产级稳定性"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(h2("1.3 跟单范围"));
const rangeItems = [
  "市价开仓与平仓操作",
  "挂单（限价单、止损单）的创建与管理",
  "持仓止损与止盈的动态修改",
  "部分平仓场景",
  "跨经纪商品种代码适配",
];
// We'll add numbering config later; use simple bullets here
rangeItems.forEach(item => children.push(bullet(item)));

children.push(hr());

// ======================================================================
// CHAPTER 2
// ======================================================================
children.push(h1("2. 需求分析"));
children.push(h2("2.1 核心功能需求"));

const cw21 = [600, CONTENT_W - 600 - 800, 800];
children.push(makeTable(
  ["编号", "需求", "优先级"],
  [
    ["F-01", "实时捕获主账户的交易操作", "P0"],
    ["F-02", "按配置比例复制交易到从账户", "P0"],
    ["F-03", "同步止损止盈的修改操作", "P0"],
    ["F-04", "同步挂单及其状态变更", "P1"],
    ["F-05", "多从账户以不同比例独立跟单", "P1"],
    ["F-06", "多层级风控保护", "P1"],
    ["F-07", "异常断连后的自动恢复与补偿", "P1"],
    ["F-08", "集中化的管理界面", "P2"],
  ],
  cw21
));

children.push(h2("2.2 关键非功能需求"));
const cw22 = [1000, CONTENT_W - 1000 - 1400, 1400];
children.push(makeTable(
  ["编号", "需求", "指标"],
  [
    ["NF-01", "跟单延迟", "毫秒级响应"],
    ["NF-02", "跟单成功率", "高可靠性（≥ 99.5%）"],
    ["NF-03", "系统可用性", "7×24 连续运行"],
    ["NF-04", "平台合规", "符合 MT 平台交易频率规范"],
    ["NF-05", "扩展能力", "支持从账户数量持续增长"],
  ],
  cw22
));

children.push(hr());

// ======================================================================
// CHAPTER 3
// ======================================================================
children.push(h1("3. 技术可行性"));
children.push(h2("3.1 与 MetaTrader 的交互方案"));
children.push(p("经过对多种方案的调研与评估，MT4 和 MT5 均可通过成熟的技术方案与 Python 进行双向通信："));
children.push(makeTable(
  ["平台", "交互方式", "评估"],
  [
    ["MT4", "EA 事件驱动 + 高性能消息中间件桥接", "工业验证方案，延迟极低"],
    ["MT5", "官方 Python API（MetaTrader5 库）", "官方支持，稳定性最佳"],
  ],
  [1000, CONTENT_W - 1000 - 1800, 1800]
));
children.push(p("两种方案均采用开源工具，无需商业许可，已在多个生产环境中得到验证。"));

children.push(h2("3.2 MetaTrader 内置 Signals 功能评估"));
children.push(p("MT4/MT5 自带的「信号」功能基于云端中继架构，评估结论如下："));
children.push(makeTable(
  ["评估维度", "结论"],
  [
    ["适用场景", "适合订阅 MQL5.com 上的公开信号源"],
    ["级联跟单", "不支持（一个账户不能同时为订阅者与提供者）"],
    ["延迟水平", "秒级，不适合对延迟敏感的场景"],
    ["定制能力", "不可自定义跟单比例、不可过滤品种、不复制挂单"],
  ],
  [2000, CONTENT_W - 2000]
));
children.push(blockquote("结论：MT 内置 Signals 功能不适合本项目的级联跟单场景，需采用自建方案。"));

children.push(h2("3.3 核心技术栈"));
children.push(makeTable(
  ["层级", "技术选型"],
  [
    ["开发语言", "Python 3.12+"],
    ["MT4 通信", "EA 事件驱动 + 高性能消息中间件"],
    ["MT5 通信", "MetaQuotes 官方 Python SDK"],
    ["异步框架", "asyncio（支持多终端并发管理）"],
    ["数据持久化", "关系型数据库（交易映射与审计）"],
    ["高速缓存", "内存数据库（快照对比与消息队列）"],
    ["部署方式", "容器化核心服务 + MT 终端独立运行"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(hr());

// ======================================================================
// CHAPTER 4
// ======================================================================
children.push(h1("4. 系统架构概览"));
children.push(h2("4.1 架构简图"));
children.push(...codeBlock([
  "┌──────────────────────────────────────────────┐",
  "│                VPS (Windows)                  │",
  "│                                               │",
  "│  ┌──────────┐       ┌──────────────────────┐ │",
  "│  │ 主账户    │──▶──│     Python 跟单服务    │ │",
  "│  │ MT 终端  │       │                      │ │",
  "│  │          │       │  信号接收 → 处理 →   │ │",
  "│  │ 交易捕获  │       │  风控 → 分发 → 记录  │ │",
  "│  └──────────┘       └────────┬─────────────┘ │",
  "│                               │               │",
  "│  ┌──────────┐                │               │",
  "│  │ 从账户 B  │◀───────────────┘               │",
  "│  │ MT 终端  │                                │",
  "│  └──────────┘       ┌────────┐ ┌────────┐   │",
  "│                      │ 数据库  │ │ 缓存   │   │",
  "│  ┌──────────┐       └────────┘ └────────┘   │",
  "│  │ 从账户 C  │◀───────────────               │",
  "│  │ ...更多   │                                │",
  "│  └──────────┘                                │",
  "└──────────────────────────────────────────────┘",
]));
children.push(new Paragraph({ spacing: { before: 120 } }));

children.push(h2("4.2 数据流简述"));
children.push(...codeBlock([
  "主账户交易事件 → 事件捕获 → 信号去重与校验",
  "    → 查询跟单配置 → 多层级风控检查",
  "    → 比例计算 → 从账户执行 → 映射记录与审计",
]));
children.push(new Paragraph({ spacing: { before: 120 } }));

children.push(h2("4.3 关键架构原则"));
children.push(makeTable(
  ["原则", "说明"],
  [
    ["本地化部署", "所有组件部署在同一台 VPS，消除网络延迟"],
    ["事件驱动", "捕获层采用事件推送而非定时轮询，实现最低延迟"],
    ["状态无关", "核心处理逻辑无状态，支持水平扩展"],
    ["故障自愈", "连接中断自动恢复 + 全量对账补偿"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(hr());

// ======================================================================
// CHAPTER 5
// ======================================================================
children.push(h1("5. 核心方案概述"));
children.push(h2("5.1 交易信号捕获"));
children.push(p("采用持仓快照差量对比法检测主账户的交易变化："));
["持续获取主账户当前持仓状态", "与缓存的历史快照进行实时比对", "差量分析自动识别开仓、平仓、修改三种操作", "内置去重机制防止同一事件被多次触发"].forEach(item => children.push(bullet(item)));

children.push(h2("5.2 跟单执行"));
children.push(p("采用配置驱动的比例复制引擎："));
["每个从账户独立配置跟单比例", "自动按品种交易规则进行手数精度截断", "支持品种代码映射（跨经纪商适配）", "内置最小下单间隔控制（符合 MT 平台规范）"].forEach(item => children.push(bullet(item)));

children.push(h2("5.3 循环触发防护"));
children.push(p("多层安全机制确保从账户自身的交易不会反向触发新的跟单："));
["下单时注入来源标记", "信号检测层自动过滤标记交易", "维护已知订单识别列表"].forEach(item => children.push(bullet(item)));

children.push(h2("5.4 异常恢复"));
children.push(p("连接中断或故障后的自动恢复机制："));
["持续心跳检测通信链路", "断连自动重连（智能退避策略）", "重连后触发全量对账：比对主从账户持仓 → 自动修复差异", "异常事件分级告警"].forEach(item => children.push(bullet(item)));

children.push(h2("5.5 风控体系"));
children.push(p("分层风控检查，每次下单前逐层验证："));
children.push(makeTable(
  ["层级", "说明"],
  [
    ["账户层", "余额检查、仓位上限、持仓数量限制"],
    ["风险层", "单日最大亏损、最大回撤控制"],
    ["系统层", "下单频率控制、全局敞口限制"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(hr());

// ======================================================================
// CHAPTER 6
// ======================================================================
children.push(h1("6. 性能概览"));
children.push(h2("6.1 延迟水平"));
children.push(makeTable(
  ["指标", "水平"],
  [
    ["信号捕获延迟", "毫秒级"],
    ["处理与执行延迟", "毫秒级"],
    ["端到端总延迟", "百毫秒级"],
    ["对标行业水平", "优于多数商业跟单方案"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(h2("6.2 吞吐能力"));
children.push(p("系统采用异步并发架构，支持同时处理多个信号事件。从账户的执行采用并行分发，跟单延迟随从账户数量增长呈亚线性增长。"));

children.push(h2("6.3 资源效率"));
children.push(p("单台中等配置 VPS 可承载多个 MT 终端实例和完整的 Python 跟单服务，资源利用率合理。"));

children.push(hr());

// ======================================================================
// CHAPTER 7
// ======================================================================
children.push(h1("7. 风险分析"));
children.push(makeTable(
  ["风险", "概率", "影响", "缓解措施"],
  [
    ["交易平台限制 DLL 加载", "低", "高", "已准备备用通信方案"],
    ["经纪商交易频率限制", "中", "中", "内置可配置的下单间隔控制"],
    ["MT 终端异常退出", "中", "高", "进程监控 + 自动拉起 + 恢复对账"],
    ["通信链路中断", "低", "中", "心跳监控 + 自动重连"],
    ["跨经纪商品种代码差异", "中", "中", "可配置品种映射表"],
    ["从账户资金不足", "中", "低", "自动降级处理"],
    ["级联循环触发", "低", "高", "多层防护机制"],
  ],
  [2000, 800, 600, CONTENT_W - 2000 - 800 - 600]
));

children.push(hr());

// ======================================================================
// CHAPTER 8
// ======================================================================
children.push(h1("8. 实施计划"));
children.push(makeTable(
  ["阶段", "核心内容", "预估工期"],
  [
    ["Phase 1", "基础框架搭建、数据模型、账户配置管理", "1 周"],
    ["Phase 2", "主账户信号捕获、从账户下单执行", "1-2 周"],
    ["Phase 3", "完整跟单功能（挂单、修改、部分平仓、品种映射）", "1 周"],
    ["Phase 4", "风控模块、自动对账、异常恢复", "1 周"],
    ["Phase 5", "模拟环境验证、生产部署、监控配置", "1 周"],
  ],
  [1200, CONTENT_W - 1200 - 1200, 1200]
));
children.push(blockquote("总工期：约 1.5-2 个月（单人全职）"));

children.push(hr());

// ======================================================================
// CHAPTER 9
// ======================================================================
children.push(h1("9. 结论与建议"));
children.push(h2("9.1 可行性结论"));
children.push(p("项目完全可行。MetaTrader 平台的 API 生态成熟，所需的通信中间件和开发框架均经过工业级验证。自建方案相比 MT 内置 Signals 功能在延迟、定制性、可靠性方面具有显著优势。"));

children.push(h2("9.2 核心建议"));
children.push(makeTable(
  ["建议", "优先级"],
  [
    ["采用事件驱动架构以实现最低的跟单延迟", "P0"],
    ["所有组件部署在同一 VPS 上，利用本地通信消除网络延迟", "P0"],
    ["先实现一对一跟单，验证稳定后扩展至一对多", "P0"],
    ["建立完善的异常监控和告警机制", "P1"],
    ["管理界面按需迭代，初期通过配置文件和日志满足基本管理需求", "P2"],
  ],
  [CONTENT_W - 1200, 1200]
));

children.push(h2("9.3 方案优势总结"));
children.push(makeTable(
  ["优势", "说明"],
  [
    ["低延迟", "事件驱动 + 本地部署，延迟远低于云端方案"],
    ["高可靠", "自动故障恢复 + 全量对账 + 多层风控"],
    ["灵活定制", "可配置跟单比例、品种过滤、风控参数"],
    ["可扩展", "从 1 到 N 的平滑扩展路径"],
    ["低成本", "全开源技术栈，仅需 VPS 费用"],
    ["级联支持", "原生支持多级跟单链路"],
  ],
  [2000, CONTENT_W - 2000]
));

children.push(hr());
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 200 },
  children: [new TextRun({ text: "如需获取包含完整技术参数和实施细节的内部版本，请联系项目负责人。", font: FONT, size: 20, italics: true, color: "666666" })],
}));

// ======================================================================
// BUILD DOCUMENT
// ======================================================================
const doc = new Document({
  styles: {
    default: {
      document: { run: { font: FONT, size: 21 } },
    },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: FONT, color: BLUE },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT, color: BLUE },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 },
      },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BLUE, space: 4 } },
          children: [new TextRun({ text: "MT 跟单同步系统 · 技术可行性报告（脱敏版）", font: FONT, size: 16, color: "888888" })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: BLUE, space: 4 } },
          children: [
            new TextRun({ text: "—  ", font: FONT, size: 16, color: "888888" }),
            new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: "888888" }),
            new TextRun({ text: "  —", font: FONT, size: 16, color: "888888" }),
          ],
        })],
      }),
    },
    children,
  }],
});

// ─── Output ─────────────────────────────────────────────
const outDir = path.join(__dirname, "..");
const outPath = path.join(outDir, "MT跟单系统-技术可行性报告-脱敏版.docx");

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outPath, buffer);
  console.log("✅ Word document created: " + outPath);
  console.log("   Size: " + (buffer.length / 1024).toFixed(1) + " KB");
}).catch(err => {
  console.error("❌ Error:", err);
  process.exit(1);
});
