const { AndroidConfig, withAndroidManifest } = require("expo/config-plugins");

/**
 * Enable cleartext HTTP traffic in standalone Android builds so users can connect
 * to both HTTPS domains and local/VPS http://<ip>:7777 Jarvis OS endpoints.
 */
module.exports = function withCleartextTraffic(config) {
  return withAndroidManifest(config, (cfg) => {
    const mainApp = AndroidConfig.Manifest.getMainApplicationOrThrow(cfg.modResults);
    mainApp.$["android:usesCleartextTraffic"] = "true";
    return cfg;
  });
};
