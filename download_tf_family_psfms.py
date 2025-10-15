#!/usr/bin/env python3
"""
CollecTF Selenium Downloader
Uses Selenium to actually click links and navigate like a real browser
"""

import contextlib
import hashlib
import json
import logging
import re
import time
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    print("❌ Selenium not installed. Please install with: pip install selenium")
    print(
        "❌ You also need ChromeDriver: brew install chromedriver (Mac) or "
        "download from https://chromedriver.chromium.org/"
    )
    exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class CollecTFSeleniumDownloader:
    def __init__(self, output_dir="data/tf_coevolution/collectf/selenium_psfms", headless=False):
        self.base_url = "http://www.collectf.org"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        self.matrices_dir = self.output_dir / "matrices"
        self.metadata_dir = self.output_dir / "metadata"
        self.progress_dir = self.output_dir / "progress"

        for dir_path in [self.matrices_dir, self.metadata_dir, self.progress_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        # Progress tracking files
        self.progress_file = self.progress_dir / "download_progress.json"
        self.completed_file = self.progress_dir / "completed_tfs.txt"
        self.family_log_file = self.progress_dir / "tf_family_results.txt"

        # Setup Chrome options with download directory
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")

        # Set up download directory
        self.download_dir = self.output_dir / "temp_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)

        prefs = {
            "download.default_directory": str(self.download_dir.absolute()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,  # Allow insecure downloads
            "safebrowsing.disable_download_protection": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
            "profile.default_content_settings.popups": 0,
            "profile.managed_default_content_settings.images": 2,
            # More aggressive insecure download settings
            "profile.default_content_setting_values.mixed_script": 1,
            "profile.default_content_setting_values.media_stream": 1,
            "download.extensions_to_open": "",
            "download.open_pdf_in_system_reader": False,
        }
        chrome_options.add_experimental_option("prefs", prefs)

        # Additional flags to allow insecure downloads
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-images")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--allow-http-screen-capture")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-renderer-backgrounding")

        # CRITICAL: These are the key flags for bypassing insecure download blocking
        chrome_options.add_argument("--disable-features=DownloadBubble,DownloadBubbleV2")
        chrome_options.add_argument("--disable-features=InsecureDownloadWarnings")
        chrome_options.add_argument("--allow-insecure-localhost")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--ignore-ssl-errors")
        chrome_options.add_argument("--ignore-certificate-errors-spki-list")
        chrome_options.add_argument("--ignore-certificate-errors-ssl-errors")
        chrome_options.add_argument("--allow-mixed-content")
        chrome_options.add_argument(
            "--unsafely-treat-insecure-origin-as-secure=http://www.collectf.org"
        )

        # Initialize the driver
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, 10)
            logger.info("✓ Chrome WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Chrome WebDriver: {e}")
            logger.error("Make sure ChromeDriver is installed and in your PATH")
            raise

    def _find_link_by_text(self, text):
        """Helper method to find a link by text, handling stale elements"""
        try:
            all_links = self.driver.find_elements(By.TAG_NAME, "a")
            for link in all_links:
                try:
                    if link.text.strip() == text:
                        return link
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def _get_current_tf_links(self):
        """Get fresh TF links from the current page"""
        try:
            all_links = self.driver.find_elements(By.TAG_NAME, "a")
            tf_links = []

            for link in all_links:
                try:
                    text = link.text.strip()
                    href = link.get_attribute("href")

                    # Skip empty or very short names
                    if len(text) < 2 or len(text) > 20:
                        continue

                    # Skip common navigation words
                    skip_words = {
                        "browse",
                        "search",
                        "about",
                        "home",
                        "view",
                        "more",
                        "here",
                        "feedback",
                        "stats",
                        "links",
                        "cite",
                        "contribute",
                        "compare",
                        "register",
                        "login",
                        "quick",
                        "help",
                        "contact",
                    }
                    if text.lower() in skip_words:
                        continue

                    # Look for TF-like names (alphanumeric, reasonable length)
                    if re.match(r"^[A-Za-z][A-Za-z0-9/]{1,15}$", text):
                        tf_links.append({"name": text, "element": link, "href": href})

                except Exception:
                    continue  # Skip problematic links

            return tf_links
        except Exception as e:
            logger.error(f"Error getting current TF links: {e}")
            return []

    def load_progress(self):
        """Load progress from previous runs"""
        completed_tfs = set()
        progress_data = {}

        # Load completed TF families
        if self.completed_file.exists():
            try:
                with open(self.completed_file) as f:
                    completed_tfs = set(line.strip() for line in f if line.strip())
                logger.info(f"Found {len(completed_tfs)} previously completed TF families")
            except Exception as e:
                logger.warning(f"Could not load completed TFs: {e}")

        # Load detailed progress data
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    progress_data = json.load(f)
                logger.info(f"Loaded progress data with {len(progress_data)} entries")
            except Exception as e:
                logger.warning(f"Could not load progress data: {e}")

        return completed_tfs, progress_data

    def save_progress(self, tf_name, status, details=None):
        """Save progress for a TF family"""
        try:
            # Load existing progress
            progress_data = {}
            if self.progress_file.exists():
                try:
                    with open(self.progress_file) as f:
                        progress_data = json.load(f)
                except Exception:
                    pass

            # Update progress
            progress_data[tf_name] = {
                "status": status,
                "timestamp": time.time(),
                "details": details or {},
            }

            # Save progress file
            with open(self.progress_file, "w") as f:
                json.dump(progress_data, f, indent=2)

            # If completed, add to completed file
            if status == "completed":
                with open(self.completed_file, "a") as f:
                    f.write(f"{tf_name}\n")

        except Exception as e:
            logger.warning(f"Could not save progress for {tf_name}: {e}")

    def save_motif_progress(self, tf_name, species_name, status, details=None):
        """Save progress for an individual motif report within a TF family"""
        try:
            # Load existing progress
            progress_data = {}
            if self.progress_file.exists():
                try:
                    with open(self.progress_file) as f:
                        progress_data = json.load(f)
                except Exception:
                    pass

            # Create motif report key
            motif_key = f"{tf_name}_{species_name}".replace(" ", "_")

            # Initialize TF family entry if it doesn't exist
            tf_family = details.get("tf_family", tf_name) if details else tf_name
            if tf_family not in progress_data:
                progress_data[tf_family] = {
                    "status": "in_progress",
                    "timestamp": time.time(),
                    "details": {"motif_reports": {}},
                }

            # Ensure motif_reports exists
            if "motif_reports" not in progress_data[tf_family]["details"]:
                progress_data[tf_family]["details"]["motif_reports"] = {}

            # Update motif report progress
            progress_data[tf_family]["details"]["motif_reports"][motif_key] = {
                "tf_name": tf_name,
                "species_name": species_name,
                "status": status,
                "timestamp": time.time(),
                "details": details or {},
            }

            # Update TF family status if all motif reports are completed
            motif_reports = progress_data[tf_family]["details"]["motif_reports"]
            if all(report["status"] == "completed" for report in motif_reports.values()):
                progress_data[tf_family]["status"] = "completed"

            # Save progress file
            with open(self.progress_file, "w") as f:
                json.dump(progress_data, f, indent=2)

        except Exception as e:
            logger.warning(f"Could not save motif progress for {tf_name} - {species_name}: {e}")

    def log_tf_family_results(self, tf_family, results):
        """Log the results for a TF family to a text file"""
        try:
            with open(self.family_log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 80}\n")
                f.write(f"TF FAMILY: {tf_family}\n")
                f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'=' * 80}\n")

                successful = []
                no_export = []
                failed = []

                for result in results:
                    tf_name = result["tf_name"]
                    species_name = result["species_name"]
                    status = result["status"]
                    entry = f"  • {tf_name} - {species_name}"

                    if status == "completed":
                        successful.append(entry)
                    elif status == "no_export":
                        no_export.append(entry)
                    else:
                        failed.append(entry)

                f.write(f"\n✅ SUCCESSFUL DOWNLOADS ({len(successful)}):\n")
                if successful:
                    for entry in successful:
                        f.write(f"{entry}\n")
                else:
                    f.write("  (none)\n")

                f.write(f"\n⚠️  NO EXPORT FUNCTIONALITY ({len(no_export)}):\n")
                if no_export:
                    for entry in no_export:
                        f.write(f"{entry}\n")
                else:
                    f.write("  (none)\n")

                f.write(f"\n❌ FAILED DOWNLOADS ({len(failed)}):\n")
                if failed:
                    for entry in failed:
                        f.write(f"{entry}\n")
                else:
                    f.write("  (none)\n")

                f.write(
                    f"\nSUMMARY: {len(successful)} successful, {len(no_export)} no export, "
                    f"{len(failed)} failed\n"
                )
                f.write(f"TOTAL PROCESSED: {len(results)}\n")

        except Exception as e:
            logger.warning(f"Could not log TF family results for {tf_family}: {e}")

    def get_resume_summary(self, completed_tfs, all_tf_names, progress_data=None):
        """Generate a summary of what will be resumed with detailed motif report info"""
        remaining = [tf for tf in all_tf_names if tf not in completed_tfs]

        if completed_tfs or progress_data:
            logger.info("\n📋 RESUME SUMMARY:")
            logger.info(f"  Total TF families: {len(all_tf_names)}")
            logger.info(f"  Already completed: {len(completed_tfs)}")
            logger.info(f"  Remaining to process: {len(remaining)}")
            progress_pct = len(completed_tfs) / len(all_tf_names) * 100
            logger.info(
                f"  Progress: {len(completed_tfs)}/{len(all_tf_names)} ({progress_pct:.1f}%)"
            )

            # Show detailed motif report progress
            if progress_data:
                total_motif_reports = 0
                completed_motif_reports = 0
                in_progress_tfs = []

                for tf_name, tf_data in progress_data.items():
                    if "motif_reports" in tf_data.get("details", {}):
                        motif_reports = tf_data["details"]["motif_reports"]
                        total_motif_reports += len(motif_reports)
                        completed_count = sum(
                            1
                            for report in motif_reports.values()
                            if report["status"] == "completed"
                        )
                        completed_motif_reports += completed_count

                        if tf_data["status"] == "in_progress":
                            in_progress_tfs.append(
                                f"{tf_name} ({completed_count}/{len(motif_reports)})"
                            )
                        elif tf_data["status"] == "completed" and len(motif_reports) > 1:
                            # Show completed TFs with multiple motif reports
                            logger.info(
                                f"    ✅ {tf_name}: {len(motif_reports)} motif reports completed"
                            )

                if total_motif_reports > 0:
                        logger.info(
                            f"  Motif reports: {completed_motif_reports}/"
                            f"{total_motif_reports} completed"
                        )

                if in_progress_tfs:
                    logger.info(f"  In progress: {', '.join(in_progress_tfs)}")

            # Show completed TFs
            if len(completed_tfs) <= 10:
                logger.info(f"  Completed TFs: {', '.join(sorted(completed_tfs))}")
            else:
                sample_completed = list(sorted(completed_tfs))[:5]
                logger.info(
                    f"  Completed TFs (sample): {', '.join(sample_completed)}... "
                    f"and {len(completed_tfs) - 5} more"
                )

        return remaining

    def navigate_to_tf_families_page(self):
        """Step 1: Navigate to TF families page"""
        try:
            tf_families_url = f"{self.base_url}/browse/browse_by_TF/"
            logger.info(f"Step 1: Navigating to TF families page: {tf_families_url}")

            self.driver.get(tf_families_url)
            time.sleep(3)  # Wait for page to load

            # Check if page loaded successfully
            page_title = self.driver.title
            logger.info(f"Page title: {page_title}")

            return True

        except Exception as e:
            logger.error(f"Error navigating to TF families page: {e}")
            return False

    def find_and_click_tf_family_links(self, max_tfs=5, completed_tfs=None, progress_data=None):
        """Step 2: Find TF family links and click them"""
        try:
            logger.info("Step 2: Looking for TF family links to click")

            # Find all links that look like TF names
            all_links = self.driver.find_elements(By.TAG_NAME, "a")

            tf_links = []
            for link in all_links:
                try:
                    text = link.text.strip()
                    href = link.get_attribute("href")

                    # Skip empty or very short names
                    if len(text) < 2 or len(text) > 20:
                        continue

                    # Skip common navigation words
                    skip_words = {
                        "browse",
                        "search",
                        "about",
                        "home",
                        "view",
                        "more",
                        "here",
                        "feedback",
                        "stats",
                        "links",
                        "cite",
                        "contribute",
                        "compare",
                        "register",
                        "login",
                        "quick",
                        "help",
                        "contact",
                    }
                    if text.lower() in skip_words:
                        continue

                    # Look for TF-like names (alphanumeric, reasonable length)
                    if re.match(r"^[A-Za-z][A-Za-z0-9/]{1,15}$", text):
                        tf_links.append({"name": text, "element": link, "href": href})

                except Exception:
                    continue  # Skip problematic links

            logger.info(f"Found {len(tf_links)} potential TF family links")
            for tf in tf_links[:10]:  # Show first 10
                logger.info(f"  {tf['name']}: {tf['href']}")

            # Filter out already completed TFs
            if completed_tfs is None:
                completed_tfs = set()

            all_tf_names = [tf["name"] for tf in tf_links]
            remaining_tf_names = self.get_resume_summary(completed_tfs, all_tf_names, progress_data)

            # Filter tf_links to only include remaining ones
            tf_links = [tf for tf in tf_links if tf["name"] in remaining_tf_names]

            # Limit to max_tfs if specified
            if max_tfs is not None:
                tf_links = tf_links[:max_tfs]
                logger.info(f"Limited to first {max_tfs} remaining TF families for testing")
            else:
                logger.info(f"Processing all {len(tf_links)} remaining TF families")

            successful_downloads = 0
            failed_tfs = []

            # Click each TF family link
            for i, tf_link in enumerate(tf_links):
                tf_name = tf_link["name"]
                logger.info(f"\n=== Processing TF Family {i + 1}/{len(tf_links)}: {tf_name} ===")

                try:
                    # Re-find the element to avoid stale reference
                    current_tf_link = None

                    # Wait a bit for page to fully load
                    time.sleep(1)

                    # Try multiple strategies to find the TF link
                    strategies = [
                        # Strategy 1: Exact text match
                        lambda: self.driver.find_element(By.LINK_TEXT, tf_name),
                        # Strategy 2: Partial text match
                        lambda: self.driver.find_element(By.PARTIAL_LINK_TEXT, tf_name),
                        # Strategy 3: Search all links manually
                        lambda: self._find_link_by_text(tf_name),
                    ]

                    for strategy in strategies:
                        try:
                            current_tf_link = strategy()
                            if current_tf_link:
                                logger.info(f"✓ Re-found TF link for {tf_name}")
                                break
                        except Exception:
                            continue

                    if not current_tf_link:
                        logger.warning(f"Could not re-find TF link for {tf_name}")
                        continue

                    # Click the TF family link
                    logger.info(f"Clicking TF family link: {tf_name}")
                    current_tf_link.click()
                    time.sleep(3)  # Wait for page to load

                    # Process species on this TF page
                    species_downloads = self.process_tf_species_page(tf_name)
                    successful_downloads += species_downloads

                    # Progress is now tracked at the motif level in process_tf_species_page
                    # Just log the summary here
                    if species_downloads > 0:
                        logger.info(
                            f"✅ Completed {tf_name}: {species_downloads} motif reports downloaded"
                        )
                    else:
                        logger.info(f"⚠️ Completed {tf_name}: no downloadable motif reports found")

                    # Go back to TF families page (refresh instead of back to avoid stale elements)
                    logger.info("Returning to TF families page...")
                    if not self.navigate_to_tf_families_page():
                        logger.error("Failed to navigate back to TF families page")
                        break
                    time.sleep(1)

                except Exception as e:
                    logger.error(f"Error processing TF family {tf_name}: {e}")
                    failed_tfs.append(tf_name)

                    # Save error progress
                    self.save_progress(tf_name, "error", {"error": str(e)})

                    # Try to go back to TF families page
                    try:
                        self.navigate_to_tf_families_page()
                    except Exception:
                        logger.error("Could not navigate back to TF families page, stopping")
                        break
                    continue

            logger.info("\n=== All TF Families Complete ===")
            logger.info(f"Total successful downloads: {successful_downloads}")

            if failed_tfs:
                logger.warning(f"Failed TF families ({len(failed_tfs)}): {', '.join(failed_tfs)}")

            # Final progress summary
            total_processed = len(tf_links)
            if total_processed > 0:
                success_rate = (successful_downloads / total_processed) * 100
                logger.info(
                    f"Success rate: {successful_downloads}/{total_processed} ({success_rate:.1f}%)"
                )

            return successful_downloads

        except Exception as e:
            logger.error(f"Error finding TF family links: {e}")
            return 0

    def process_tf_species_page(self, tf_name):
        """Step 3: Process the TF species page and find 'view' links"""
        try:
            logger.info(f"Step 3: Processing species page for TF {tf_name}")

            # Look for "view" links on this page
            view_links = []

            # Find all links with "view" text
            all_links = self.driver.find_elements(By.TAG_NAME, "a")

            for link in all_links:
                try:
                    text = link.text.strip().lower()
                    href = link.get_attribute("href")

                    if "view" in text and href and "view_motif_reports_by_TF_and_species" in href:
                        # Extract TF and species IDs from URL
                        match = re.search(
                            r"/view_motif_reports_by_TF_and_species/(\d+)/(\d+)/", href
                        )
                        if match:
                            tf_id, species_id = match.groups()

                            # Extract individual TF name and species name from the table row
                            individual_tf_name = tf_name  # fallback to family name
                            species_name = "Unknown"
                            try:
                                row = link.find_element(By.XPATH, "./ancestor::tr")
                                cells = row.find_elements(By.TAG_NAME, "td")

                                if len(cells) >= 3:  # Should have TF, Species, View columns
                                    # First column: individual TF name
                                    tf_cell_text = cells[0].text.strip()
                                    if tf_cell_text and len(tf_cell_text) < 20:
                                        individual_tf_name = tf_cell_text

                                    # Second column: species name
                                    species_cell_text = cells[1].text.strip()
                                    if species_cell_text and len(species_cell_text) > 5:
                                        species_name = species_cell_text

                                logger.info(f"  Found: {individual_tf_name} - {species_name}")
                            except Exception as e:
                                logger.debug(f"Error extracting row data: {e}")

                            view_links.append(
                                {
                                    "tf_name": individual_tf_name,  # Individual TF name
                                    "tf_family": tf_name,  # Family name (AraC/XylS)
                                    "tf_id": int(tf_id),
                                    "species_name": species_name,
                                    "species_id": int(species_id),
                                    "href": href,  # Store only the URL, not the element
                                }
                            )

                except Exception:
                    continue

            logger.info(f"Found {len(view_links)} 'view' links for {tf_name}")

            successful_downloads = 0
            family_results = []  # Track results for logging

            # Click each view link
            for j, view_link in enumerate(view_links):
                species_name = view_link["species_name"]
                tf_individual_name = view_link["tf_name"]
                logger.info(
                    f"\n--- Processing Species {j + 1}/{len(view_links)}: {species_name} ---"
                )

                try:
                    # Navigate directly to the motif report page
                    logger.info(f"Navigating to motif report for {tf_name} - {species_name}")
                    self.driver.get(view_link["href"])
                    time.sleep(3)  # Wait for motif report page to load

                    # Process the motif report page
                    result = self.process_motif_report_page(view_link)

                    # Track result for logging
                    family_results.append(
                        {
                            "tf_name": tf_individual_name,
                            "species_name": species_name,
                            "status": result,
                        }
                    )

                    if result == "completed":
                        successful_downloads += 1
                        self.save_motif_progress(
                            tf_individual_name,
                            species_name,
                            "completed",
                            {"downloaded": True, "tf_family": tf_name},
                        )
                    elif result == "no_export":
                        self.save_motif_progress(
                            tf_individual_name,
                            species_name,
                            "no_export",
                            {"reason": "no export functionality", "tf_family": tf_name},
                        )
                    else:
                        self.save_motif_progress(
                            tf_individual_name,
                            species_name,
                            "no_data",
                            {"reason": "download failed", "tf_family": tf_name},
                        )

                    # Go back to TF species page
                    logger.info("Going back to TF species page...")
                    self.driver.back()
                    time.sleep(2)

                except Exception as e:
                    logger.error(f"Error processing species {species_name}: {e}")
                    # Track failed result
                    family_results.append(
                        {
                            "tf_name": tf_individual_name,
                            "species_name": species_name,
                            "status": "failed",
                        }
                    )
                    continue

            # Log results for this TF family
            self.log_tf_family_results(tf_name, family_results)

            return successful_downloads

        except Exception as e:
            logger.error(f"Error processing TF species page for {tf_name}: {e}")
            return 0

    def process_motif_report_page(self, view_link):
        """Step 4: Process motif report page and download PSFM"""
        try:
            tf_name = view_link["tf_name"]
            species_name = view_link["species_name"]

            logger.info(f"Step 4: Processing motif report for {tf_name} - {species_name}")

            # Check if this page has export functionality
            page_source = self.driver.page_source

            export_indicators = {
                "Export data": "Export data" in page_source,
                "PSFM": "PSFM" in page_source,
                "site_id": "site_id" in page_source,
                "csrfmiddlewaretoken": "csrfmiddlewaretoken" in page_source,
                "Download PSFM": "Download PSFM" in page_source,
            }

            logger.info("Export functionality check:")
            for indicator, present in export_indicators.items():
                logger.info(f"  {indicator}: {'✓' if present else '✗'}")

            # Only proceed if we have the key export indicators
            if not (export_indicators["Export data"] and export_indicators["csrfmiddlewaretoken"]):
                logger.warning("Motif page lacks export functionality")
                return "no_export"

            # Look for the "Export data" tab or section
            try:
                # Try to find and click "Export data" tab
                export_tab = self.driver.find_element(By.PARTIAL_LINK_TEXT, "Export data")
                logger.info("Found 'Export data' tab, clicking...")
                export_tab.click()
                time.sleep(5)  # Wait longer for dynamic content to load

                # Wait for the export content to appear
                logger.info("Waiting for export content to load...")
                time.sleep(3)

            except NoSuchElementException:
                logger.info("No 'Export data' tab found, assuming export form is already visible")

            # Look for PSFM download link (it's a text link, not a button)
            try:
                # Look for the PSFM raw-FASTA download link
                psfm_link = None

                # Look for the actual clickable download link
                psfm_patterns = ["Download PSFM (raw-FASTA)", "Download PSFM"]

                # First try to find the direct link by text
                for pattern in psfm_patterns:
                    try:
                        psfm_link = self.driver.find_element(By.LINK_TEXT, pattern)
                        logger.info(f"✓ Found direct download link: '{pattern}'")
                        break
                    except NoSuchElementException:
                        try:
                            psfm_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, pattern)
                            logger.info(f"✓ Found partial download link: '{pattern}'")
                            break
                        except NoSuchElementException:
                            continue

                # If direct link not found, search all links for PSFM
                if not psfm_link:
                    logger.info("Direct link not found, searching all links...")
                    all_links = self.driver.find_elements(By.TAG_NAME, "a")

                    logger.info(f"Found {len(all_links)} total links on the page")

                    # Show all links for debugging
                    for i, link in enumerate(all_links):
                        try:
                            link_text = link.text.strip()
                            href = link.get_attribute("href")
                            if link_text:  # Only show non-empty links
                                logger.info(f"  Link {i + 1}: '{link_text}' -> {href}")

                                # Check for PSFM patterns
                                if any(pattern in link_text for pattern in psfm_patterns):
                                    psfm_link = link
                                    logger.info(f"✓ Found PSFM link: '{link_text}'")
                                    break
                        except Exception as e:
                            logger.debug(f"Error checking link {i}: {e}")
                            continue

                # If still not found, look for the specific PSFM raw-FASTA text in table cells
                if not psfm_link:
                    try:
                        # Look for the exact text we saw in the debug output
                        psfm_text = (
                            "Download Position-Specific-Frequency-Matrix of the motif "
                            "in raw FASTA format"
                        )
                        psfm_element = self.driver.find_element(
                            By.XPATH, f"//td[contains(text(), '{psfm_text}')]"
                        )

                        if psfm_element:
                            logger.info(f"✓ Found PSFM table cell with text: '{psfm_text[:50]}...'")
                            # This table cell should be clickable
                            psfm_link = psfm_element

                    except NoSuchElementException:
                        # Fallback: look for any td containing "raw FASTA format"
                        try:
                            psfm_element = self.driver.find_element(
                                By.XPATH, "//td[contains(text(), 'raw FASTA format')]"
                            )
                            if psfm_element:
                                logger.info("✓ Found PSFM table cell with 'raw FASTA format'")
                                psfm_link = psfm_element
                        except NoSuchElementException:
                            pass

                if psfm_link:
                    logger.info("Found PSFM download link, clicking...")
                    logger.info(f"Element tag: {psfm_link.tag_name}")
                    logger.info(f"Element text: {psfm_link.text[:100]}...")

                    # Clear any existing downloads
                    import glob

                    existing_files = glob.glob(str(self.download_dir / "*"))
                    logger.info(f"Cleared {len(existing_files)} existing files from download dir")

                    # Check if the element is actually clickable
                    try:
                        # Try to scroll to element first
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", psfm_link)
                        time.sleep(1)

                        # Check if there's a form or button we should click instead
                        logger.info("Looking for form elements near the PSFM text...")

                        # Look for the actual download button/link in the same row
                        try:
                            # Find the parent row
                            parent_row = psfm_link.find_element(By.XPATH, "./ancestor::tr")

                            # Look for any clickable elements in this row
                            clickable_elements = parent_row.find_elements(
                                By.XPATH, ".//input[@type='submit'] | .//button | .//a"
                            )

                            if clickable_elements:
                                logger.info(
                                    f"Found {len(clickable_elements)} clickable elements "
                                    "in the same row"
                                )
                                for i, elem in enumerate(clickable_elements):
                                    try:
                                        elem_text = (
                                            elem.text
                                            or elem.get_attribute("value")
                                            or elem.get_attribute("name")
                                        )
                                        logger.info(
                                            f"  Clickable {i + 1}: {elem.tag_name} - '{elem_text}'"
                                        )

                                        # If this looks like a download button, use it instead
                                        if any(
                                            word in elem_text.lower()
                                            for word in ["download", "psfm", "fasta"]
                                        ):
                                            logger.info(
                                                f"Using clickable element instead: {elem_text}"
                                            )
                                            psfm_link = elem
                                            break
                                    except Exception:
                                        continue
                        except Exception:
                            logger.info("No parent row found or no clickable elements in row")

                        # Click the download link
                        psfm_link.click()
                        logger.info("Clicked download element, waiting for file...")

                        # Give it a moment to process
                        time.sleep(2)

                    except Exception as click_error:
                        logger.error(f"Error clicking element: {click_error}")
                        return "failed"

                    # Wait for download to complete
                    downloaded_file = self.wait_for_download()

                    if downloaded_file:
                        logger.info(f"✓ Download completed: {downloaded_file}")

                        # Read the downloaded file
                        try:
                            with open(downloaded_file) as f:
                                psfm_content = f.read().strip()

                            if psfm_content and len(psfm_content) > 10:
                                logger.info(f"✓ Read PSFM content ({len(psfm_content)} chars)")

                                # Save to our organized directory structure
                                success = self.save_psfm_content(view_link, psfm_content)

                                # Clean up the temporary download
                                with contextlib.suppress(Exception):
                                    downloaded_file.unlink()

                                return success
                            else:
                                logger.warning("Downloaded file appears to be empty or invalid")
                                return "failed"

                        except Exception as e:
                            logger.error(f"Error reading downloaded file: {e}")
                            return "failed"
                    else:
                        logger.warning("No file was downloaded")
                        return "failed"
                else:
                    logger.warning("No PSFM download link found")

                    # Debug: Show all available links on the page
                    logger.info("Available links on export page:")
                    all_page_links = self.driver.find_elements(By.TAG_NAME, "a")
                    for link in all_page_links:
                        try:
                            link_text = link.text.strip()
                            if link_text and len(link_text) > 0:
                                logger.info(f"  Link: '{link_text}'")
                        except Exception:
                            continue

                    # Also check for any elements containing "Download" or "PSFM"
                    logger.info("Elements containing 'Download' or 'PSFM':")
                    download_elements = self.driver.find_elements(
                        By.XPATH, "//*[contains(text(), 'Download') or contains(text(), 'PSFM')]"
                    )
                    for elem in download_elements:
                        try:
                            elem_text = elem.text.strip()
                            if elem_text:
                                logger.info(f"  Element ({elem.tag_name}): '{elem_text}'")
                        except Exception:
                            continue

                    return "failed"

            except Exception as e:
                logger.error(f"Error clicking PSFM download button: {e}")
                return "failed"

        except Exception as e:
            logger.error(f"Error processing motif report page: {e}")
            return "failed"

    def extract_psfm_content_from_page(self, page_source):
        """Extract PSFM content from page source"""
        try:
            # Look for PSFM matrix patterns
            # PSFM content usually starts with ">" (FASTA header) or has tab-separated numbers

            # Try to find content between <pre> tags (common for matrix display)
            pre_match = re.search(r"<pre[^>]*>(.*?)</pre>", page_source, re.DOTALL | re.IGNORECASE)
            if pre_match:
                content = pre_match.group(1).strip()
                # Check if it looks like PSFM content
                if (">" in content and "\n" in content) or (
                    "\t" in content and any(c.isdigit() for c in content)
                ):
                    logger.info("Found PSFM content in <pre> tags")
                    return content

            # Try to find FASTA-like content
            fasta_match = re.search(r"(>[^\n]+\n(?:[0-9\t\s]+\n?)+)", page_source, re.MULTILINE)
            if fasta_match:
                content = fasta_match.group(1).strip()
                logger.info("Found FASTA-like PSFM content")
                return content

            logger.warning("Could not extract PSFM content from page")
            return None

        except Exception as e:
            logger.error(f"Error extracting PSFM content: {e}")
            return None

    def save_psfm_content(self, view_link, psfm_content):
        """Save PSFM content to file"""
        try:
            tf_name = view_link["tf_name"]  # Individual TF name (AdpA, AraC, etc.)
            tf_family = view_link["tf_family"]  # Family name (AraC/XylS)
            species_name = view_link["species_name"]
            tf_id = view_link["tf_id"]
            species_id = view_link["species_id"]

            # Generate safe filename components
            safe_tf_family = re.sub(r"[^\w\-_]", "_", tf_family)
            safe_tf_name = re.sub(r"[^\w\-_]", "_", tf_name)
            safe_species_name = re.sub(r"[^\w\-_\s]", "_", species_name).replace(" ", "_")

            # Create a hash for uniqueness
            content_hash = hashlib.md5(psfm_content.encode()).hexdigest()[:8]

            # Format: Family_IndividualTF_Species_IDs_method_hash.txt
            filename = (
                f"{safe_tf_family}_{safe_tf_name}_{safe_species_name}_"
                f"TF{tf_id}_SP{species_id}_selenium_{content_hash}.txt"
            )

            # Save the PSFM
            filepath = self.matrices_dir / filename
            with open(filepath, "w") as f:
                f.write(psfm_content)

            # Save metadata
            metadata = {
                "tf_name": tf_name,  # Individual TF name
                "tf_family": tf_family,  # Family name
                "tf_id": tf_id,
                "species_name": species_name,
                "species_id": species_id,
                "filename": filename,
                "download_timestamp": time.time(),
                "content_length": len(psfm_content),
                "method": "selenium_browser_automation",
                "url": self.driver.current_url,
            }

            metadata_file = self.metadata_dir / f"{filename}.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"✓ Downloaded: {tf_name} - {species_name} -> {filename}")
            return "completed"

        except Exception as e:
            logger.error(f"Error saving PSFM content: {e}")
            return "failed"

    def download_all_tf_families(self, max_tfs=None):
        """Main function: Download PSFMs using browser automation"""

        logger.info("=== CollecTF Selenium PSFM Downloader ===")
        logger.info("Using browser automation to click links exactly like a human")

        try:
            # Load progress from previous runs
            completed_tfs, progress_data = self.load_progress()

            # Step 1: Navigate to TF families page
            if not self.navigate_to_tf_families_page():
                logger.error("Failed to navigate to TF families page")
                return 0

            # Step 2: Find and click TF family links
            successful_downloads = self.find_and_click_tf_family_links(
                max_tfs, completed_tfs, progress_data
            )

            logger.info("\n=== Download Complete ===")
            logger.info(f"Total successful downloads: {successful_downloads}")
            logger.info(f"Output directory: {self.output_dir}")
            logger.info(f"Matrices saved in: {self.matrices_dir}")

            return successful_downloads

        except Exception as e:
            logger.error(f"Error in main download process: {e}")
            return 0
        finally:
            # Always close the browser
            self.close()

    def wait_for_download(self, timeout=30):
        """Wait for a file to be downloaded and return the file path"""
        import glob

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Look for any new files in the download directory
            files = glob.glob(str(self.download_dir / "*"))

            # Filter out .crdownload files (Chrome partial downloads)
            complete_files = [f for f in files if not f.endswith(".crdownload")]

            if complete_files:
                # Return the most recently created file
                newest_file = max(complete_files, key=lambda f: Path(f).stat().st_mtime)
                return Path(newest_file)

            time.sleep(0.5)

        logger.warning(f"No download completed within {timeout} seconds")
        return None

    def close(self):
        """Close the browser"""
        try:
            self.driver.quit()
            logger.info("✓ Browser closed")
        except Exception as e:
            logger.error(f"Error closing browser: {e}")


def main():
    """Main function"""

    # Create downloader (set headless=False to see the browser in action)
    downloader = CollecTFSeleniumDownloader(headless=False)

    try:
        # Download from first 3 TF families for testing
        logger.info("Starting Selenium-based download for ALL TF families...")
        successful_downloads = downloader.download_all_tf_families()  # Process all TF families

        if successful_downloads > 0:
            logger.info(
                f"\n🎉 SUCCESS! Downloaded {successful_downloads} PSFMs using browser automation!"
            )
        else:
            logger.error("\n❌ No successful downloads")

    except KeyboardInterrupt:
        logger.info("\n⚠️ Download interrupted by user")
    except Exception as e:
        logger.error(f"\n❌ Download failed: {e}")
    finally:
        downloader.close()


if __name__ == "__main__":
    main()
