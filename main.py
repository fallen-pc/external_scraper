from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncio
import os
import re
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Your Custom Grays Scraper", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Vehicle(BaseModel):
    url: str
    title: str = ""
    make: str = "Unknown"
    model: str = "Unknown"
    year: Optional[int] = None
    variant: Optional[str] = ""
    body_type: Optional[str] = ""
    no_of_seats: Optional[int] = None
    vin: Optional[str] = ""
    fuel_type: Optional[str] = ""
    transmission: Optional[str] = ""
    odometer_reading: Optional[int] = None
    exterior_colour: Optional[str] = ""
    location: Optional[str] = ""
    price: Optional[float] = None
    bids: Optional[int] = 0
    time_remaining_or_date_sold: Optional[str] = ""
    status: str = "active"
    general_condition: Optional[str] = ""
    features_list: Optional[str] = ""

class UpdateRequest(BaseModel):
    urls: List[str]

# In-memory storage
vehicles_db = []
vehicle_links = []

# ─── YOUR LINK EXTRACTION CODE (ADAPTED) ───────────────────────
def extract_all_vehicle_links(max_pages=10):
    """Your working link extractor adapted for FastAPI"""
    BASE_URL = "https://www.grays.com/search/automotive-trucks-and-marine/motor-vehiclesmotor-cycles/motor-vehicles"
    all_links = []
    page = 1
    seen = set()

    logger.info("Starting link extraction...")

    while page <= max_pages:
        url = f"{BASE_URL}?tab=items&isdesktop=1&page={page}"
        logger.info(f"Fetching page {page}: {url}")
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Page {page} failed: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        page_links = []

        for a in soup.find_all("a", href__=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if re.search(r"/lot/\d+", href) and re.match(r"^\d{4}\b", text):
                if any(bike in text.lower() for bike in ["motorbike", "motor bike", "quad"]):
                    continue
                full_url = "https://www.grays.com" + href if href.startswith("/") else href
                if full_url not in seen:
                    seen.add(full_url)
                    page_links.append(full_url)

        if not page_links:
            logger.info("No more links found. Stopping.")
            break

        logger.info(f"Page {page}: Found {len(page_links)} new links")
        all_links.extend(page_links)
        page += 1

    logger.info(f"Total unique links found: {len(all_links)}")
    return all_links

# ─── YOUR DETAIL EXTRACTION CODE (ADAPTED) ───────────────────────
def clean_joined_fields(text):
    return re.sub(r'([a-z])([A-Z])', r'\1, \2', text)

def extract_field(soup, label):
    li_tags = soup.find_all("li")
    for li in li_tags:
        text = li.get_text(strip=True)
        if re.match(rf"^{re.escape(label)}\s*:", text, re.IGNORECASE):
            parts = text.split(":", 1)
            if len(parts) == 2:
                return clean_joined_fields(parts[1].strip())
    return "N/A"

def extract_general_condition(soup):
    try:
        condition_header = soup.find('strong', string=re.compile("condition assessment", re.IGNORECASE))
        if condition_header:
            ul = condition_header.find_parent('p').find_next_sibling('ul')
            items = [li.get_text(strip=True) for li in ul.find_all('li')] if ul else []
            return '\n'.join(items) if items else 'N/A'
    except:
        pass
    return 'N/A'

def extract_features_list(soup):
    try:
        features_header = soup.find('strong', string=re.compile("^features", re.IGNORECASE))
        if features_header:
            ul = features_header.find_parent('p').find_next_sibling('ul')
            items = [li.get_text(strip=True) for li in ul.find_all('li')] if ul else []
            return ', '.join(items) if items else 'N/A'
    except:
        pass
    return 'N/A'

def extract_location(soup):
    try:
        location_cell = soup.find('td', string=re.compile('Location', re.IGNORECASE))
        if location_cell:
            location_text = location_cell.find_next_sibling('td').get_text(strip=True)
            region = location_text.split(',')[-2].strip().upper()
            if region in ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT']:
                return region
    except:
        pass
    return 'N/A'

def extract_vehicle_details(soup, url):
    """Your working detail extractor adapted for FastAPI"""
    title_elem = soup.find("h1", class_="dls-heading-3 lotPageTitle")
    title = title_elem.get_text(strip=True) if title_elem else "N/A"
    title_parts = title.split()
    
    year = title_parts[0] if title_parts and re.match(r"^\d{4}$", title_parts[0]) else None
    make = title_parts[1] if len(title_parts) > 1 else "Unknown"
    model = title_parts[2] if len(title_parts) > 2 else "Unknown"
    variant = " ".join(title_parts[3:]) if len(title_parts) > 3 else ""

    # Extract basic fields using your field mapping
    FIELD_MAP = {
        "body_type": "Body Type",
        "no_of_seats": "No. of Seats",
        "vin": "VIN",
        "fuel_type": "Fuel Type",
        "transmission": "Transmission",
        "odometer_reading": "Indicated Odometer Reading",
        "exterior_colour": "Exterior Colour",
    }

    details = {
        "url": url,
        "title": title,
        "year": int(year) if year and year.isdigit() else None,
        "make": make,
        "model": model,
        "variant": variant,
        "status": "active"
    }

    # Extract fields using your mapping
    for field_key, label in FIELD_MAP.items():
        value = extract_field(soup, label)
        if field_key == "no_of_seats" and value != "N/A":
            try:
                details[field_key] = int(value)
            except:
                details[field_key] = None
        elif field_key == "odometer_reading" and value != "N/A":
            try:
                # Extract numbers from odometer reading
                numbers = re.findall(r'\d+', value.replace(',', ''))
                details[field_key] = int(numbers[0]) if numbers else None
            except:
                details[field_key] = None
        else:
            details[field_key] = value if value != "N/A" else ""

    details["general_condition"] = extract_general_condition(soup)
    details["features_list"] = extract_features_list(soup)
    details["location"] = extract_location(soup)

    return details

async def safe_goto(page, url, timeout=60000, retries=2):
    """Your safe goto function"""
    for attempt in range(retries):
        try:
            await page.goto(url, timeout=timeout)
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            await page.wait_for_timeout(2000)
    return False

async def process_links_with_playwright(links, max_vehicles=50):
    """Your Playwright processing adapted for FastAPI"""
    results = []
    skipped = []
    
    # Limit to avoid timeout issues
    links_to_process = links[:max_vehicles]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for i, url in enumerate(links_to_process):
            logger.info(f"Scraping {i+1}/{len(links_to_process)}: {url}")
            
            if await safe_goto(page, url):
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")
                vehicle_data = extract_vehicle_details(soup, url)
                results.append(vehicle_data)
            else:
                logger.warning(f"Skipping {url} after retries")
                skipped.append(url)
                
        await browser.close()

    logger.info(f"Processed {len(results)} vehicles, skipped {len(skipped)}")
    return results

# ─── FASTAPI ENDPOINTS ───────────────────────────────────────────
@app.get("/")
async def root():
    return {"message": "Your Custom Grays Scraper API", "status": "running"}

@app.post("/api/scrape")
async def trigger_scrape():
    """Run your complete scraping process"""
    global vehicle_links, vehicles_db
    
    try:
        logger.info("Starting complete scraping process...")
        
        # Step 1: Extract links using your link collector
        logger.info("Extracting vehicle links...")
        vehicle_links = extract_all_vehicle_links(max_pages=5)  # Limit pages for Railway
        
        if not vehicle_links:
            return {"message": "No vehicle links found", "found": 0, "processed": 0}
        
        # Step 2: Process some links with Playwright (limit for performance)
        logger.info(f"Processing details for {min(20, len(vehicle_links))} vehicles...")
        vehicles_data = await process_links_with_playwright(vehicle_links, max_vehicles=20)
        
        # Step 3: Store in memory database
        vehicles_db = vehicles_data
        
        return {
            "message": "Scraping completed successfully",
            "found": len(vehicle_links), 
            "processed": len(vehicles_db),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {e}")

@app.get("/api/vehicles")
async def get_vehicles():
    """Return all scraped vehicles in Base44 format"""
    logger.info(f"Returning {len(vehicles_db)} vehicles to Base44")
    
    # Convert to Base44 Vehicle format
    base44_vehicles = []
    for vehicle_data in vehicles_db:
        base44_vehicles.append({
            "url": vehicle_data["url"],
            "title": vehicle_data["title"],
            "make": vehicle_data["make"],
            "model": vehicle_data["model"],
            "year": vehicle_data["year"],
            "variant": vehicle_data.get("variant", ""),
            "body_type": vehicle_data.get("body_type", ""),
            "seats": vehicle_data.get("no_of_seats"),
            "vin": vehicle_data.get("vin", ""),
            "fuel_type": vehicle_data.get("fuel_type", ""),
            "transmission": vehicle_data.get("transmission", ""),
            "odometer": vehicle_data.get("odometer_reading"),
            "exterior_color": vehicle_data.get("exterior_colour", ""),
            "location": vehicle_data.get("location", ""),
            "status": vehicle_data.get("status", "active"),
            "features": vehicle_data.get("features_list", "").split(", ") if vehicle_data.get("features_list") else []
        })
    
    return base44_vehicles

@app.post("/api/update-listings")  
async def update_listings(request: UpdateRequest):
    """Update price/bid info for specific URLs (placeholder)"""
    logger.info(f"Update requested for {len(request.urls)} URLs")
    
    # TODO: Add your price updating logic here if you have one
    # For now, return placeholder data
    updated_vehicles = []
    for url in request.urls:
        updated_vehicles.append({
            "url": url,
            "price": None,  # Add your price scraping logic
            "bids": 0,      # Add your bid scraping logic  
            "time_remaining": "Unknown",  # Add your time scraping logic
            "status": "active"
        })
    
    return updated_vehicles

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)