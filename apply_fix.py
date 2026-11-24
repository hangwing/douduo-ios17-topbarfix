#!/usr/bin/env python3
"""
DouDuo v1.3.14 补丁脚本（两项）
  1. 顶栏垂直位置适配非 Duo 机型（iPhone 15 Pro 等）—— 对应上游 issue #16
  2. SDK 26 兼容层：裁掉上游依赖 iOS 27 SDK 的 API 分支（iOS 17.0 上这些分支本来就不执行）

用法:  python3 apply_fix.py <DouDuo 源码根目录>
"""
import sys, os, io

ROOT = sys.argv[1] if len(sys.argv) > 1 else '.'
SRC = os.path.join(ROOT, 'DouDuo')


def load(name):
    with io.open(os.path.join(SRC, name), encoding='utf-8') as f:
        return f.read()


def save(name, text):
    with io.open(os.path.join(SRC, name), 'w', encoding='utf-8') as f:
        f.write(text)


class Editor:
    def __init__(self, name):
        self.name = name
        self.s = load(name)
        self.edits = 0

    def sub(self, old, new, tag):
        n = self.s.count(old)
        if n != 1:
            raise SystemExit('FAIL %s / %s: expected 1 match, got %d' % (self.name, tag, n))
        self.s = self.s.replace(old, new, 1)
        self.edits += 1

    def cut(self, start_marker, end_marker, replacement, tag):
        """用起止标记替换一整段，避免多行字符串转义问题。"""
        i = self.s.index(start_marker)
        j = self.s.index(end_marker, i)
        self.s = self.s[:i] + replacement + self.s[j:]
        self.edits += 1

    def flush(self):
        save(self.name, self.s)
        print('  %-26s %d edits' % (self.name, self.edits))


# ================================================================ 1. 布局度量 + SDK shim
e = Editor('DouyinDuoLayout.swift')
e.sub('import SwiftUI\n', 'import SwiftUI\nimport UIKit\n', 'import UIKit')

e.sub('enum DYDuoMetrics {', '''// MARK: - SDK 26 构建 shim
// toolbarVerticalEdge / overlayArrangementZIndex 是 iOS 27 SDK 才有的环境键。
// 用 Xcode 26（iPhoneOS 26.x SDK）编译时补上同名定义；运行时在 iOS 17 不走这些分支。
private struct DYShimToolbarVerticalEdgeKey: EnvironmentKey {
    static let defaultValue: HorizontalEdge? = nil
}
extension EnvironmentValues {
    var toolbarVerticalEdge: HorizontalEdge? {
        get { self[DYShimToolbarVerticalEdgeKey.self] }
        set { self[DYShimToolbarVerticalEdgeKey.self] = newValue }
    }
}
private struct DYShimOverlayArrangementZIndexKey: EnvironmentKey {
    static let defaultValue: Int = 1
}
extension EnvironmentValues {
    var overlayArrangementZIndex: Int {
        get { self[DYShimOverlayArrangementZIndexKey.self] }
        set { self[DYShimOverlayArrangementZIndexKey.self] = newValue }
    }
}

enum DYDuoMetrics {''', 'env shim')

e.sub('''    /// iOS 17–27.0 的宽屏回退，和改版前同一条线。
    static let legacyWideMin: CGFloat = 700
''', '''    /// iOS 17–27.0 的宽屏回退，和改版前同一条线。
    static let legacyWideMin: CGFloat = 700

    /// 是不是 iPhone Duo 真机（iPhone19,4）。
    /// 顶栏那套「整层忽略安全区 + 上推 80」只对 Duo 外屏成立：Duo 外屏窗口内容区比物理屏顶低约 80pt，
    /// 推 80 正好贴顶。普通 iPhone（15 Pro 等）窗口从物理顶端开始，再推 80 就把「关注/推荐/直播」
    /// 推进状态栏和灵动岛里（竖屏 −4pt，横屏同样在屏外），点不到。上游 issue #16。
    static let isDuoHardware: Bool = {
        #if targetEnvironment(simulator)
        let id = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] ?? ""
        #else
        var s = utsname()
        uname(&s)
        let id = withUnsafeBytes(of: &s.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
        #endif
        return id == "iPhone19,4"
    }()

    /// 窗口顶部安全区（状态栏 / 灵动岛）。这一层整块 ignoresSafeArea，GeometryProxy 量到的 top 恒为 0，
    /// 只能直接读 keyWindow。横屏状态栏隐藏时返回 0。
    @MainActor static var windowSafeTop: CGFloat {
        UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first?.safeAreaInsets.top ?? 0
    }
''', 'isDuoHardware/windowSafeTop')

e.sub('''        if #available(iOS 27.1, *) {
            return proxy.reservedRegions(kind: .division).contains { $0.isActive }
        }
        return false''',
'''        // SDK 26 构建：reservedRegions 属于 iOS 27 SDK；iOS 17 本来也走这条路
        return false''', 'foldIsActive')

e.sub('''    static func topBarLeftShift(_ proxy: GeometryProxy) -> CGFloat {
        guard #available(iOS 27.1, *) else { return 0 }
        let w = proxy.size.width
        var shift: CGFloat = 0
        for r in proxy.reservedRegions(kind: .occlusion) where r.isActive {
            let f = r.frame
            guard f.height > 1, f.width > 1 else { continue }
            // 顶部 140pt 内、且位于右半边 → 认定为前摄
            if f.minY < 140, f.minX > w * 0.5 {
                shift = max(shift, f.width)
            }
        }
        return min(shift, 56)
    }''',
'''    static func topBarLeftShift(_ proxy: GeometryProxy) -> CGFloat {
        // SDK 26 构建：依赖 iOS 27 的 occlusion 区；非 Duo 机型上这个量本来就是 0
        return 0
    }''', 'topBarLeftShift')

# 整段替换 offset(in:...) 的函数体（否则 return 之后残留死代码仍引用 reservedRegions）
e.cut('    static func offset(in proxy: GeometryProxy',
      '    private static func clear(',
      '''    static func offset(in proxy: GeometryProxy, railEdge: HorizontalEdge? = nil,
                       layoutDirection: LayoutDirection = .leftToRight, verticalSpace: ClosedRange<CGFloat>? = nil) -> CGSize {
        // SDK 26 构建：reservedRegions 只有 iOS 27 SDK 才有，这里不做平移
        _ = (proxy, railEdge, layoutDirection, verticalSpace)
        return .zero
    }

''', 'nudge offset')
e.flush()


# ================================================================ 2. 顶栏位置 + 折痕
e = Editor('DouyinFeedView.swift')
e.sub('''    @State private var topNudge: CGFloat = 0
''', '''    @State private var topNudge: CGFloat = 0
    /// 默认模式顶栏的整体垂直偏移。这一层已 ignoresSafeArea（原点 = 物理屏顶），
    /// 而 topBar 自己还带内边距（compact 76pt / regular 23pt），所以把内边距扣掉，
    /// 让算的是「关注/推荐/直播」这几个字的顶边落点。
    ///
    /// - iPhone Duo：内屏 0（与系统时间同行）、外屏 -80（外屏窗口比物理顶低 80pt）→ 原样保留
    /// - 其他 iPhone / iPad：竖屏落在安全区下沿 +6pt；横屏状态栏隐藏（safeTop=0）落在离顶 14pt
    /// 15 Pro 代入：竖屏 59+6−76 = −11 → 文字顶边 65pt（灵动岛下沿 48）；
    ///              横屏 14−76 = −62 → 文字顶边 14pt（原来 −4，出屏）
    @MainActor private var topBarTopPadding: CGFloat {
        if DYDuoMetrics.isDuoHardware { return hSize == .regular ? 0 : -80 }
        let inner: CGFloat = hSize == .regular ? 23 : 76
        let safeTop = DYDuoMetrics.windowSafeTop
        let target: CGFloat = safeTop < 1 ? 14 : safeTop + 6
        return target - inner
    }
''', 'topBarTopPadding')

e.sub('                .padding(.top, hSize == .regular ? 0 : -80)',
      '                .padding(.top, topBarTopPadding)', 'topBar padding')

e.sub('''        guard DYDuoMetrics.foldIsActive(geo) else { return nil }
        guard #available(iOS 27.1, *) else { return nil }
        let divisions = geo.reservedRegions(kind: .division).map {
            DYLiveDivision(frame: $0.frame, isActive: $0.isActive)
        }
        guard let p = DYLiveAdaptiveLayoutEngine.hingePanes(
            bounds: CGRect(origin: .zero, size: geo.size), divisions: divisions) else { return nil }
        return (p.first, p.second, p.book)''',
'''        guard DYDuoMetrics.foldIsActive(geo) else { return nil }
        // SDK 26 构建：拿不到 reservedRegions，无真实折痕几何，调用方回退到比例切分
        return nil''', 'hingeSplit')

e.sub('''        guard #available(iOS 27.1, *) else { return 0 }
        var inset: CGFloat = 0
        for region in geo.reservedRegions(kind: .occlusion) where region.isActive {
            let f = region.frame
            guard f.width > 1, f.height > 1, f.intersects(pane), f.midX > pane.midX else { continue }
            inset = max(inset, pane.maxX - f.minX)
        }
        return min(max(0, inset), 120)''',
'''        // SDK 26 构建：occlusion 区取不到，不做内缩
        _ = (geo, pane)
        return 0''', 'trailingOcclusionInset')
e.flush()


# ================================================================ 3. 倍速角标
e = Editor('ShortVideoPlayer.swift')
e.sub('                .padding(.leading, 14).padding(.top, 66 * ui)',
      '''                .padding(.leading, 14)
                .padding(.top, rateBadgeTop)''', 'badge padding')

e.sub('    private var progressBar: some View {',
      '''    /// 倍速角标离屏顶的距离。
    /// Duo 外屏状态区约 85pt，66 * ui 正好落在顶栏下方；其他机型（15 Pro 等）顶栏被挪到安全区下沿，
    /// 角标必须跟着走，否则会跟顶栏叠在一起。
    @MainActor private var rateBadgeTop: CGFloat {
        if DYDuoMetrics.isDuoHardware { return 66 * ui }
        return max(DYDuoMetrics.windowSafeTop, 12) + 56
    }

    private var progressBar: some View {''', 'rateBadgeTop')
e.flush()


# ================================================================ 4. 直播折痕
e = Editor('DouyinLiveRoom.swift')
e.cut('@available(iOS 27.1, *)\nprivate func dyLiveHingeStatus(',
      'private extension View {',
      '''// SDK 26 构建：DeviceHinge / onHingeChange 属于 iOS 27 SDK，这里不编译铰链回调

''', 'dyLiveHingeStatus')

e.sub('''        if #available(iOS 27.1, *) {
            onHingeChange { _, context in apply(dyLiveHingeStatus(context.hinge)) }
        } else {
            self
        }''',
'''        // SDK 26 构建：没有 onHingeChange，铰链状态由 DYLiveAdaptiveLayoutEngine 按宽高推断
        _ = apply
        self''', 'onLiveHingeChange')

e.sub('''        guard #available(iOS 27.1, *) else { return [] }
        return geo.reservedRegions(kind: .division).map {
            DYLiveDivision(frame: $0.frame, isActive: $0.isActive)
        }''',
'''        // SDK 26 构建：取不到折痕分区，按整屏处理
        _ = geo
        return []''', 'liveDivisions')

e.sub('''        if #available(iOS 27.1, *) {
            for region in geo.reservedRegions(kind: .occlusion) where region.isActive {
                regions.append(region.frame)
            }
        }''',
'''        // SDK 26 构建：取不到 occlusion 区''', 'occlusion append')

e.sub('''        guard #available(iOS 27.1, *) else { return [] }
        var frames: [CGRect] = []
        for region in geo.reservedRegions(kind: .division) where region.isActive {
            frames.append(region.frame)
        }
        return frames''',
'''        // SDK 26 构建：取不到折痕分区
        _ = geo
        return []''', 'previewDivisions')
e.flush()


# ================================================================ 5. 消息分屏
e = Editor('DouyinMessages.swift')
start_marker = '        if #available(iOS 27.1, *) {\n            ArrangementView {'
tail = '        } else {\n            legacyMessageContent\n        }'
i = e.s.index(start_marker)
j = e.s.index(tail, i)
block = e.s[i:j]
assert 'ArrangementView' in block and 'legacyMessageContent' not in block, 'unexpected block'
e.s = (e.s[:i]
       + '        // SDK 26 构建：ArrangementView 是 iOS 27 SDK 的 API，统一走全屏覆盖回退\n'
         '        legacyMessageContent\n'
       + e.s[j + len(tail):])
e.edits += 1
e.flush()


# ================================================================ 6. 缓存初始化
e = Editor('FollowLiveLogic.swift')
e.sub('''    private var storedAt: Date?
    private var items: [DYFollowMetadata] = []

    mutating func store''',
'''    private var storedAt: Date?
    private var items: [DYFollowMetadata] = []

    init(ttl: TimeInterval) {
        self.ttl = ttl
    }

    mutating func store''', 'init(ttl:)')
e.flush()

print('DONE')
