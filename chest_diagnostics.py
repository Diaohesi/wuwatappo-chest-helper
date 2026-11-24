"""Read-only recognition diagnostics; no screenshots or input are persisted."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import platform
import re
import sys

import chest_background as bg
from chest_detection import detect_templates
from chest_matcher import find_match
import chest_windows as desktop


MAX_BEST_SCORE_PIXELS = 2_500_000


def _safe_text(value):
    text = str(value)
    text = re.sub(r"[A-Za-z]:[\\/][^\r\n\"'<>]*", "<本地路径>", text)
    return re.sub(r"(?<!\w)/(?:Users|home|tmp|private|opt)/[^\s\"'<>]*", "<本地路径>", text)


def _error(stage, exc, hwnd=None):
    result = {"stage": stage, "type": type(exc).__name__, "message": _safe_text(exc)}
    if hwnd is not None:
        result["hwnd"] = int(hwnd)
    return result


def _os_report():
    result = {"system": platform.system(), "release": platform.release(),
              "version": platform.version(), "machine": platform.machine()}
    if hasattr(sys, "getwindowsversion"):
        result["build"] = sys.getwindowsversion().build
    return result


def _dpi_report():
    result = {"thread_context": None, "thread_awareness": None, "errors": []}
    try:
        api = desktop._user32.GetThreadDpiAwarenessContext
        api.argtypes, api.restype = [], ctypes.c_void_p
        context = api()
        result["thread_context"] = ctypes.c_ssize_t(context or 0).value
        api = desktop._user32.GetAwarenessFromDpiAwarenessContext
        api.argtypes, api.restype = [ctypes.c_void_p], ctypes.c_int
        result["thread_awareness"] = int(api(context))
    except Exception as exc:
        result["errors"].append(_error("thread_dpi", exc))
    return result


def _window_report(window):
    result = {"hwnd": int(window.hwnd), "pid": int(window.pid), "title": _safe_text(window.title),
              "rect": list(window.rect), "class": None, "dpi": None, "errors": [], "image_status": "not_scanned"}
    try:
        result["class"] = _safe_text(desktop._window_class(window.hwnd))
    except Exception as exc:
        result["errors"].append(_error("window_class", exc, window.hwnd))
    try:
        api = desktop._user32.GetDpiForWindow
        api.argtypes, api.restype = [wintypes.HWND], wintypes.UINT
        value = int(api(window.hwnd))
        result["dpi"] = value or None
    except Exception as exc:
        result["errors"].append(_error("window_dpi", exc, window.hwnd))
    return result


def _point_owner(x, y):
    result = {"hwnd": None, "pid": None, "class": None, "root_hwnd": None,
              "root_pid": None, "root_class": None, "errors": []}
    try:
        hwnd = desktop._user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not hwnd:
            return result
        hwnd = int(hwnd)
        result["hwnd"], result["pid"] = hwnd, desktop._pid(hwnd)
        root = int(desktop._user32.GetAncestor(hwnd, 2) or hwnd)
        result["root_hwnd"], result["root_pid"] = root, desktop._pid(root)
        for key, handle in (("class", hwnd), ("root_class", root)):
            try:
                result[key] = _safe_text(desktop._window_class(handle))
            except Exception as exc:
                result["errors"].append(_error("owner_class", exc, handle))
    except Exception as exc:
        result["errors"].append(_error("point_owner", exc))
    return result


def _contains(bounds, box):
    left, top, right, bottom = bounds
    x, y, width, height = box
    return bool(width > 0 and height > 0 and left <= x < x+width <= right and top <= y < y+height <= bottom)


def _search(image, templates, threshold):
    matches = detect_templates(image, templates, threshold)
    best = []
    skipped = not matches and image.width*image.height > MAX_BEST_SCORE_PIXELS
    if not matches and not skipped:
        # Manual diagnostics only: inspect each embedded native-size variant
        # within the pixel budget, without guessing UI scales or clicking.
        for variant, template in templates:
            match = find_match(image, template, scales=(1.0,))
            if match is not None:
                best.append((variant, match))
    return matches, best, skipped


def _candidate(variant, match, window, crop_origin, bounds, image_size, mode, paired_inputs, *, best=False, layout=()):
    x, y = crop_origin[0]+match.x, crop_origin[1]+match.y
    box = int(x), int(y), int(match.width), int(match.height)
    center = int(crop_origin[0]+match.center[0]), int(crop_origin[1]+match.center[1])
    in_capture = (_contains(bounds, box) and (not layout or desktop.displays.covered(
                  (x, y, x+match.width, y+match.height), (m.rect for m in layout)))
                  and _contains((crop_origin[0], crop_origin[1], crop_origin[0]+image_size[0], crop_origin[1]+image_size[1]), box))
    result = {"variant": variant, "score": round(float(match.score), 6), "box": list(box),
              "center": list(center), "window_hwnd": int(window.hwnd), "window_pid": int(window.pid),
              "in_capture": in_capture, "hit_test_passed": None, "owner": None,
              "accepted": False, "rejection": None}
    if not in_capture:
        result["rejection"] = "outside_capture"
        return result
    result["owner"] = _point_owner(*center)
    try:
        result["hit_test_passed"] = bool(desktop.point_hits_window(*center, window))
    except Exception as exc:
        result["hit_test_error"] = _error("hit_test", exc, window.hwnd)
    if best:
        result["rejection"] = "below_threshold"
    elif mode == "borderless" and (window.hwnd, window.pid) not in paired_inputs:
        result["rejection"] = "not_input_window"
    elif mode != "background" and result["hit_test_passed"] is not True:
        result["rejection"] = "hit_test_rejected"
    else:
        result["accepted"] = True
    return result


def _finish(report):
    stats = report["stats"]
    stats["window_count"] = len(report["windows"])
    stats["pair_count"] = len(report["pairs"])
    stats["capture_count"] = len(report["captures"])
    stats["raw_candidate_count"] = len(report["candidates"])
    stats["valid_candidate_count"] = sum(c["in_capture"] for c in report["candidates"])
    stats["recognized_count"] = sum(c["accepted"] for c in report["candidates"])
    stats["hit_test_rejected_count"] = sum(c["rejection"] == "hit_test_rejected" for c in report["candidates"])
    stats["outside_capture_count"] = sum(not c["in_capture"] for c in report["candidates"])
    stats["unique_pattern_count"] = len({tuple(c["box"]) for c in report["candidates"] if c["in_capture"]})
    stats["capture_error_count"] = sum(e["stage"] in ("desktop_capture", "background_capture", "desktop_unavailable") for e in report["errors"])
    stats["window_error_count"] = sum(bool(w["errors"]) for w in report["windows"])
    if stats["recognized_count"]:
        reason = "recognized"
    elif not report["windows"]:
        reason = "no_windows"
    elif report["mode"] in ("background", "borderless") and not report["pairs"]:
        reason = "no_pairs"
    elif stats["searched_rect_count"] == 0 and report["errors"]:
        reason = "capture_error"
    elif stats["hit_test_rejected_count"]:
        reason = "hit_test_rejected"
    else:
        reason = "no_pattern"
    report["reason"], report["recognized"] = reason, reason == "recognized"
    return report


def collect_diagnostics(templates, threshold, mode, crop_window) -> dict:
    """Collect window/capture/match evidence without input or saved pixels.

    ``crop_window(image, origin, window)`` may pad a partly off-screen window.
    Every match must still fit wholly inside the original real capture bounds.
    """
    report = {"schema_version": 1, "mode": mode, "threshold": float(threshold),
              "input_sent": False, "screenshots_saved": False, "os": _os_report(),
              "desktop_available": None, "privilege_problem": None, "dpi": _dpi_report(),
              "virtual_screen": None, "templates": [{"variant": variant, "size": list(image.size)} for variant, image in templates],
              "windows": [], "pairs": [], "captures": [], "candidates": [], "best_matches": [],
              "stats": {"searched_rect_count": 0, "best_score_skipped_rect_count": 0},
              "best_score_max_pixels": MAX_BEST_SCORE_PIXELS, "errors": []}
    try:
        report["desktop_available"] = bool(desktop.is_desktop_available())
    except Exception as exc:
        report["errors"].append(_error("desktop_availability", exc))
    try:
        report["virtual_screen"] = list(desktop.virtual_rect())
        report["monitors"] = [vars(m) for m in desktop.displays.monitors()]
    except Exception as exc:
        report["errors"].append(_error("virtual_screen", exc))
    try:
        windows = desktop.find_pet_windows()
    except Exception as exc:
        report["errors"].append(_error("window_discovery", exc))
        return _finish(report)
    report["windows"] = [_window_report(window) for window in windows]
    by_key = {(w["hwnd"], w["pid"]): w for w in report["windows"]}
    try:
        report["privilege_problem"] = _safe_text(problem) if (problem := desktop.input_privilege_problem(windows)) else None
    except Exception as exc:
        report["errors"].append(_error("privilege_query", exc))
    try:
        pairs = bg.find_background_pairs(windows)
        report["pairs"] = [{"render_hwnd": r.hwnd, "input_hwnd": i.hwnd, "pid": r.pid} for r, i in pairs]
    except Exception as exc:
        pairs = []
        report["errors"].append(_error("pair_discovery", exc))
    if not windows:
        return _finish(report)
    if mode not in ("background", "borderless", "visible"):
        report["errors"].append(_error("configuration", ValueError("未知的诊断模式。")))
        return _finish(report)
    if report["desktop_available"] is not True:
        report["errors"].append(_error("desktop_unavailable", RuntimeError("桌面已锁定或不可读取。")))
        return _finish(report)
    paired_inputs = {(window.hwnd, window.pid) for _, window in pairs}

    def search_window(image, window, origin, bounds, cache, cache_key, layout=()):
        record = by_key[(window.hwnd, window.pid)]
        try:
            if cache_key not in cache:
                cache[cache_key] = _search(image, templates, threshold)
                report["stats"]["searched_rect_count"] += 1
                if cache[cache_key][2]:
                    report["stats"]["best_score_skipped_rect_count"] += 1
            matches, best, _ = cache[cache_key]
            record["image_status"] = "ok"
            for variant, match in matches:
                report["candidates"].append(_candidate(variant, match, window, origin, bounds,
                    image.size, mode, paired_inputs, layout=layout))
            for variant, match in best:
                report["best_matches"].append(_candidate(variant, match, window, origin, bounds,
                    image.size, mode, paired_inputs, best=True, layout=layout))
        except Exception as exc:
            record["image_status"] = "error"
            failure = _error("window_recognition", exc, window.hwnd)
            record["errors"].append(failure)
            report["errors"].append(failure)

    if mode == "background":
        if not pairs:
            return _finish(report)
        manager = None
        try:
            manager = bg.BackgroundCapture()
            for render, input_window in pairs:
                try:
                    frame = manager.grab(render, input_window)
                    origin = frame.origin if getattr(frame, "origin", None) is not None else bg.client_origin(render)
                    bounds = (origin[0], origin[1], origin[0]+frame.image.width, origin[1]+frame.image.height)
                    report["captures"].append({"source": "background", "hwnd": render.hwnd,
                        "size": list(frame.image.size), "origin": list(origin), "bounds": list(bounds)})
                    # Different windows with identical rects can contain different
                    # pixels; only desktop-image rects are shared across HWNDs.
                    search_window(frame.image, input_window, origin, bounds, {}, render.rect)
                except Exception as exc:
                    failure = _error("background_capture", exc, render.hwnd)
                    report["errors"].append(failure)
                    by_key[(render.hwnd, render.pid)]["errors"].append(failure)
                    by_key[(render.hwnd, render.pid)]["image_status"] = "error"
        except Exception as exc:
            report["errors"].append(_error("background_capture", exc))
        finally:
            if manager is not None:
                try:
                    manager.close()
                except Exception as exc:
                    report["errors"].append(_error("capture_close", exc))
    else:
        try:
            image, origin = desktop.capture_desktop(windows)
            layout = image.info.get('monitor_layout', ())
            if layout:
                report['monitors'] = [vars(m) for m in layout]
            bounds = (origin[0], origin[1], origin[0]+image.width, origin[1]+image.height)
            report["captures"].append({"source": "desktop", "size": list(image.size),
                "origin": list(origin), "bounds": list(bounds)})
        except Exception as exc:
            report["errors"].append(_error("desktop_capture", exc))
            return _finish(report)
        crops, searches = {}, {}
        for window in windows:
            record = by_key[(window.hwnd, window.pid)]
            try:
                if window.rect not in crops:
                    crops[window.rect] = crop_window(image, origin, window)
                crop = crops[window.rect]
                if crop is None:
                    raise RuntimeError("窗口与真实截图范围没有可识别的交集。")
                search_window(crop, window, window.rect[:2], bounds, searches, window.rect, layout)
            except Exception as exc:
                record["image_status"] = "error"
                failure = _error("window_crop", exc, window.hwnd)
                record["errors"].append(failure)
                report["errors"].append(failure)
    return _finish(report)


def summarize_diagnostics(report) -> str:
    """Explain each failure stage without presenting messages as click proof."""
    stats, reason = report.get("stats", {}), report.get("reason")
    descriptions = {
        "no_windows": "未找到可读取的 WuwaTappo 窗口；请确认桌宠正在运行且未最小化。",
        "no_pairs": "找到了桌宠，但未找到唯一匹配的渲染窗口和 DesktopCatInput 输入层。",
        "capture_error": "窗口已发现，但无法取得可用于识别的画面；请查看报告中的错误。",
        "no_pattern": "画面已读取，但没有达到相似度阈值的宝箱图案。",
        "hit_test_rejected": "已匹配到宝箱图案，但点击位置的窗口归属检查未通过；可能被遮挡或开启了穿透。",
        "recognized": f"已识别到 {stats.get('recognized_count', 0)} 个符合当前模式条件的宝箱候选。",
    }
    lines = [descriptions.get(reason, "诊断尚未得到完整结果。"),
             f"窗口 {stats.get('window_count', 0)}；配对 {stats.get('pair_count', 0)}；"
             f"截图 {stats.get('capture_count', 0)}；原始候选 {stats.get('raw_candidate_count', 0)}；"
             f"命中被拒 {stats.get('hit_test_rejected_count', 0)}。"]
    best = [candidate["score"] for candidate in report.get("best_matches", []) if candidate.get("in_capture")]
    if best:
        lines.append(f"固定原始尺寸下最佳相似度 {max(best):.3f}，当前阈值 {report.get('threshold', 0):.3f}。")
    if stats.get("best_score_skipped_rect_count"):
        lines.append("因图像较大，已跳过低分最佳相似度评估以限制内存；正常阈值识别仍已运行。")
    if stats.get("outside_capture_count"):
        lines.append("已排除超出真实截图范围的候选，离屏填充不作为识别证据。")
    if report.get("privilege_problem"):
        lines.append("检测到助手与桌宠权限不匹配；本次诊断没有申请或修改权限。")
    if report.get("errors"):
        lines.append(f"记录了 {len(report['errors'])} 项读取错误，其他窗口结果仍保留。")
    lines.append("本次只读诊断未发送点击，也未保存截图。")
    return "\n".join(lines)
