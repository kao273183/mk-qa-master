import SwiftUI
import WebKit

/// Wraps a `WKWebView` that loads the bundled reCAPTCHA-shaped fixture
/// (`index.html`) and exposes the response token to native code via the
/// `mkqa` JS message handler.
///
/// The fixture itself is unchanged from the Playwright dogfood at
/// `examples/sample_captcha_fixture/`; this view just gives Maestro a
/// real iOS WebView to drive instead of a desktop browser.
struct FixtureWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isInspectable = true  // Safari Web Inspector + Maestro selectors

        guard let indexURL = Bundle.main.url(
            forResource: "index",
            withExtension: "html",
            subdirectory: "FixtureHTML"
        ) else {
            fatalError("FixtureHTML/index.html not found in app bundle")
        }
        // Grant the bundled directory read access so the iframe.html
        // referenced from index.html can also load.
        let bundleDir = indexURL.deletingLastPathComponent()
        webView.loadFileURL(indexURL, allowingReadAccessTo: bundleDir)
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
