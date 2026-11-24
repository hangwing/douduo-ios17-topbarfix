#!/usr/bin/env python3
"""DouDuo v1.3.14 patch: (1) top bar for non-Duo iPhones (issue #16),
(2) SDK 26 compat: drop upstream branches that need iOS 27 SDK APIs.
Usage: python3 apply_fix.py <source root>"""
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
        i = self.s.index(start_marker)
        j = self.s.index(end_marker, i)
        self.s = self.s[:i] + replacement + self.s[j:]
        self.edits += 1

    def flush(self):
        save(self.name, self.s)
        print('  %-26s %d edits' % (self.name, self.edits))


# ---------------- 1. layout metrics + SDK shim
e = Editor('DouyinDuoLayout.swift')
e.sub('import SwiftUI\n', 'import SwiftUI\nimport UIKit\n', 'import UIKit')

e.sub('enum DYDuoMetrics {', '''// MARK: - SDK 26 build shim
// toolbarVerticalEdge / overlayArrangementZIndex land in the iOS 27 SDK.
// Provide them here so the project compiles with Xcode 26 (iPhoneOS 26.x SDK).
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
''', '''    /// iOS 17-27.0 wide fallback.
    static let legacyWideMin: CGFloat = 700

    /// True only on iPhone Duo hardware (iPhone19,4).
    /// The "-80" top bar offset assumes the Duo cover screen window starts ~80pt below
    /// the physical top. On a regular iPhone the window starts at the physical top, so
    /// pushing 80pt drives the tab row into the status bar / Dynamic Island. Issue #16.
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

    /// Window top safe area. This layer ignores the safe area, so GeometryProxy reports
    /// top == 0 here; read the key window instead. Returns 0 when the status bar is hidden.
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
'''        // SDK 26 build: reservedRegions needs the iOS 27 SDK
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
        // SDK 26 build: needs iOS 27 occlusion regions
        return 0
    }''', 'topBarLeftShift')

e.cut('    static func offset(in proxy: GeometryProxy',
      '    private static func clear(',
      '''    static func offset(in proxy: GeometryProxy, railEdge: HorizontalEdge? = nil,
                       layoutDirection: LayoutDirection = .leftToRight, verticalSpace: ClosedRange<CGFloat>? = nil) -> CGSize {
        // SDK 26 build: reservedRegions unavailable, no nudge
        _ = (proxy, railEdge, layoutDirection, verticalSpace)
        return .zero
    }

''', 'nudge offset')
e.flush()


# ---------------- 2. top bar position + hinge
e = Editor('DouyinFeedView.swift')
e.sub('''    @State private var topNudge: CGFloat = 0
''', '''    @State private var topNudge: CGFloat = 0
    /// Vertical offset of the default-mode top bar. This layer ignores the safe area,
    /// so the origin is the physical screen top; topBar itself still carries inner padding
    /// (76pt compact / 23pt regular), which we subtract so this lands the *text* top edge.
    ///  - iPhone Duo: 0 inner screen, -80 cover screen -> unchanged
    ///  - other iPhones / iPad: 6pt below the safe area; 14pt from the top when the
    ///    status bar is hidden (landscape)
    /// iPhone 15 Pro: portrait 59+6-76 = -11 -> text top 65pt; landscape 14-76 = -62 -> 14pt
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
        // SDK 26 build: no hinge geometry, caller falls back to a ratio split
        return nil''', 'hingeSplit')

e.sub('''        guard #available(iOS 27.1, *) else { return 0 }
        var inset: CGFloat = 0
        for region in geo.reservedRegions(kind: .occlusion) where region.isActive {
            let f = region.frame
            guard f.width > 1, f.height > 1, f.intersects(pane), f.midX > pane.midX else { continue }
            inset = max(inset, pane.maxX - f.minX)
        }
        return min(max(0, inset), 120)''',
'''        // SDK 26 build: no occlusion regions, no inset
        _ = (geo, pane)
        return 0''', 'trailingOcclusionInset')
e.flush()


# ---------------- 3. rate badge
e = Editor('ShortVideoPlayer.swift')
e.sub('                .padding(.leading, 14).padding(.top, 66 * ui)',
      '''                .padding(.leading, 14)
                .padding(.top, rateBadgeTop)''', 'badge padding')

e.sub('    private var progressBar: some View {',
      '''    /// Distance of the rate badge from the screen top. 66 * ui fits the Duo cover
    /// status area; on other iPhones the top bar moved down, so the badge follows it.
    @MainActor private var rateBadgeTop: CGFloat {
        if DYDuoMetrics.isDuoHardware { return 66 * ui }
        return max(DYDuoMetrics.windowSafeTop, 12) + 56
    }

    private var progressBar: some View {''', 'rateBadgeTop')
e.flush()


# ---------------- 4. live room hinge
e = Editor('DouyinLiveRoom.swift')
e.cut('@available(iOS 27.1, *)\nprivate func dyLiveHingeStatus(',
      'private extension View {',
      '''// SDK 26 build: DeviceHinge / onHingeChange require the iOS 27 SDK

''', 'dyLiveHingeStatus')

e.sub('''        if #available(iOS 27.1, *) {
            onHingeChange { _, context in apply(dyLiveHingeStatus(context.hinge)) }
        } else {
            self
        }''',
'''        // SDK 26 build: no onHingeChange; the engine infers posture from size
        self''', 'onLiveHingeChange')

e.sub('''        guard #available(iOS 27.1, *) else { return [] }
        return geo.reservedRegions(kind: .division).map {
            DYLiveDivision(frame: $0.frame, isActive: $0.isActive)
        }''',
'''        // SDK 26 build: no hinge divisions, treat as one screen
        _ = geo
        return []''', 'liveDivisions')

e.sub('''        if #available(iOS 27.1, *) {
            for region in geo.reservedRegions(kind: .occlusion) where region.isActive {
                regions.append(region.frame)
            }
        }''',
'''        // SDK 26 build: no occlusion regions''', 'occlusion append')

e.sub('''        guard #available(iOS 27.1, *) else { return [] }
        var frames: [CGRect] = []
        for region in geo.reservedRegions(kind: .division) where region.isActive {
            frames.append(region.frame)
        }
        return frames''',
'''        // SDK 26 build: no hinge divisions
        _ = geo
        return []''', 'previewDivisions')
e.flush()


# ---------------- 5. messages split
e = Editor('DouyinMessages.swift')
start_marker = '        if #available(iOS 27.1, *) {\n            ArrangementView {'
tail = '        } else {\n            legacyMessageContent\n        }'
i = e.s.index(start_marker)
j = e.s.index(tail, i)
block = e.s[i:j]
assert 'ArrangementView' in block and 'legacyMessageContent' not in block, 'unexpected block'
e.s = (e.s[:i]
       + '        // SDK 26 build: ArrangementView needs the iOS 27 SDK; always use the overlay fallback\n'
         '        legacyMessageContent\n'
       + e.s[j + len(tail):].lstrip('\n'))
e.edits += 1
e.flush()


# ---------------- 6. cache init
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
