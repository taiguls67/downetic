import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// hop out of tests/js so this works from any cwd, no path roulette.
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const localesDirectory = path.join(projectRoot, "downloader_app", "locales");
const installerDirectory = path.join(projectRoot, "installer");

async function readProjectFile(...segments) {
  return readFile(path.join(projectRoot, ...segments), "utf8");
}

async function main() {
  const packageSource = await readProjectFile("downloader_app", "__init__.py");
  const installerSource = await readProjectFile("installer", "Downetic.iss");
  const version = packageSource.match(/^\s*__version__\s*=\s*["']([^"']+)["']/m)?.[1];
  const installerVersion = installerSource.match(/^#define AppVersion "([^"]+)"/m)?.[1];

  if (!version || installerVersion !== version) {
    throw new Error(
      `Application and installer versions do not match: ${version ?? "missing"} vs ${installerVersion ?? "missing"}.`,
    );
  }

  const localeFiles = (await readdir(localesDirectory))
    .filter((filename) => filename.endsWith(".json"))
    .sort();
  const english = JSON.parse(await readFile(path.join(localesDirectory, "en.json"), "utf8"));
  const expectedKeys = Object.keys(english).sort();

  // keep every translation in lockstep with english.
  for (const filename of localeFiles) {
    const locale = JSON.parse(await readFile(path.join(localesDirectory, filename), "utf8"));
    const actualKeys = Object.keys(locale).sort();
    if (JSON.stringify(actualKeys) !== JSON.stringify(expectedKeys)) {
      throw new Error(`Locale keys do not match English: ${filename}.`);
    }
    if (Object.values(locale).some((value) => typeof value !== "string")) {
      throw new Error(`Locale contains a non-string value: ${filename}.`);
    }
    const expectedLocale = filename.slice(0, -5).replaceAll("_", "-");
    if (locale.locale !== expectedLocale) {
      throw new Error(`Locale metadata does not match its filename: ${filename}.`);
    }
  }

  const languageSectionStart = installerSource.indexOf("[Languages]");
  if (languageSectionStart < 0) {
    throw new Error("Installer language section is missing.");
  }
  // only scan this block; the rest of the .iss file isn't language config.
  const nextSectionStart = installerSource.indexOf("\n[", languageSectionStart + 1);
  const languageSection = installerSource.slice(
    languageSectionStart,
    nextSectionStart < 0 ? undefined : nextSectionStart,
  );
  const languages = [...languageSection.matchAll(/^Name: "([^"]+)".*LicenseFile: "([^"]+)"/gm)];
  if (languages.length < 7) {
    throw new Error(`Expected at least seven installer languages, found ${languages.length}.`);
  }

  for (const [, language, licensePath] of languages) {
    const licenseFile = path.resolve(installerDirectory, licensePath);
    const relativeLicense = path.relative(installerDirectory, licenseFile);
    // don't let a sneaky path escape the installer folder.
    if (relativeLicense.startsWith("..") || path.isAbsolute(relativeLicense)) {
      throw new Error(`Installer license path escapes the installer directory: ${licensePath}.`);
    }
    const license = await readFile(licenseFile, "utf8");
    if (!license.trim()) {
      throw new Error(`Installer license is empty for ${language}: ${licensePath}.`);
    }
  }

  if (!installerSource.includes("AppVerName=Downetic v{#AppVersion}")) {
    throw new Error("Installer display name does not include the Downetic version.");
  }

  console.log(
    `Release metadata verified: Downetic ${version}, ${localeFiles.length} app locales, ${languages.length} installer languages.`,
  );
}

main().catch((error) => {
  console.error(`Release verification failed: ${error.message}`);
  process.exitCode = 1;
});
