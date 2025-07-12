from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
import asyncio
from bs4 import BeautifulSoup
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Grays Auction Scraper", version="1.2.0")

# Realistic browser headers
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

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
    title: str
    make: str
    model: str
    year: Optional[int] = None
    price: Optional[float] = None
    bids: Optional[int] = 0
    time_remaining_or_date_sold: Optional[str] = ""
    status: str = "active"

class UpdateRequest(BaseModel):
    urls: List[str]

class UpdateResponse(BaseModel):
    url: str
    price: Optional[float]
    bids: int
    time_remaining: str
    status: str

# In-memory storage
vehicles_db = []

def extract_vehicle_links(html_content: str) -> List[str]:
    """Extract vehicle links from Grays search page using the actual HTML structure."""
    soup = BeautifulSoup(html_content, 'html.parser')
    links = set()
    
    # Method 1: Find links by href pattern (most reliable)
    all_links = soup.find_all('a', href__=True)
    for link in all_links:
        href = link.get('href')
        if href and '/lot/' in href and '/motor-vehicles' in href:
            # Get the title text to verify it's a vehicle
            title_elem = link.find('h2')
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                # Check if title starts with a year (4 digits)
                if re.match(r'^\d{4}\s', title_text):
                    full_url = href if href.startswith('http') else f"https://www.grays.com{href}"
                    links.add(full_url)
                    logger.info(f"Found vehicle: {title_text[:50]}...")
    
    # Method 2: Fallback - find by ID pattern
    if not links:
        logger.warning("Method 1 found no links, trying fallback method...")
        for link in all_links:
            href = link.get('href')
            link_id = link.get('id')
            if href and link_id and link_id.startswith('LOT_'):
                full_url = href if href.startswith('http') else f"https://www.grays.com{href}"
                links.add(full_url)
    
    logger.info(f"Total unique vehicle links found: {len(links)}")
    return list(links)

def extract_vehicle_details(html_content: str, url: str) -> Vehicle:
    """Extract detailed vehicle information from individual page"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Extract title from h1 or any heading
    title_elem = soup.find('h1') or soup.find('h2') or soup.find('h3')
    title = title_elem.get_text(strip=True) if title_elem else "Unknown Vehicle"
    title_parts = title.split()

    # Extract price
    price = None
    price_elem = soup.find("span", attrs={"itemprop": "price"})
    if not price_elem:
        # Fallback: look for $ followed by numbers
        price_pattern = re.search(r'\$([0-9,]+)', soup.get_text())
        if price_pattern:
            price_text = price_pattern.group(1).replace(',', '')
            price = float(price_text) if price_text.isdigit() else None
    else:
        price_text = re.sub(r'[^\d.]', '', price_elem.get_text())
        price = float(price_text) if price_text else None
    
    # Extract bids
    bids = 0
    bid_text = soup.get_text()
    bid_match = re.search(r'(\d+)\s+bids?', bid_text, re.IGNORECASE)
    if bid_match:
        bids = int(bid_match.group(1))
    
    # Extract time remaining
    time_remaining = "Unknown"
    time_elem = soup.find("span", id="lot-closing-countdown")
    if time_elem:
        time_remaining = time_elem.get_text(strip=True)
    else:
        # Look for time pattern in text
        time_match = re.search(r'(\d+h\s+\d+m\s+\d+s|\d+\s+days?|\d+\s+hours?)', soup.get_text())
        if time_match:
            time_remaining = time_match.group(1)
    
    # Determine status
    status = "active"
    if "auction ended" in soup.get_text().lower() or not re.search(r'\d', time_remaining):
        status = "sold" if price and bids > 0 else "referred"
    
    return Vehicle(
        url=url,
        title=title,
        make=title_parts[1] if len(title_parts) > 1 else "Unknown",
        model=title_parts[2] if len(title_parts) > 2 else "Unknown", 
        year=int(title_parts[0]) if title_parts and title_parts[0].isdigit() else None,
        price=price,
        bids=bids,
        time_remaining_or_date_sold=time_remaining,
        status=status,
    )

@app.get("/")
async def root():
    return {"message": "Grays Auction Scraper API v1.2", "status": "running"}

@app.post("/api/scrape")
async def trigger_scrape():
    global vehicles_db
    try:
        logger.info("Starting enhanced scrape process...")
        vehicles_db = []
        
        base_url = "https://www.grays.com/search/automotive-trucks-and-marine/motor-vehiclesmotor-cycles/motor-vehicles"
        all_links = set()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Scrape first 5 pages to get more data
            for page in range(1, 6):
                try:
                    url = f"{base_url}?tab=items&isdesktop=1&page={page}"
                    logger.info(f"Scraping page {page}: {url}")
                    
                    response = await client.get(url, headers=HEADERS)
                    response.raise_for_status()
                    
                    # Debug: Log part of the response to see what we're getting
                    if page == 1:
                        logger.info(f"First 500 chars of page 1: {response.text[:500]}")
                    
                    links = extract_vehicle_links(response.text)
                    if not links and page == 1:
                        logger.error("No links found on the first page. Check if Grays blocked us or changed structure.")
                        # Log more of the response for debugging
                        logger.error(f"Page title: {BeautifulSoup(response.text, 'html.parser').find('title')}")
                        break
                    
                    all_links.update(links)
                    logger.info(f"Page {page}: Found {len(links)} new links. Total unique: {len(all_links)}")
                    
                    # Be nice to the server
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Error scraping page {page}: {e}")
        
        logger.info(f"Scraping complete. Total unique links found: {len(all_links)}")
        
        # Process details for first 20 vehicles (to avoid timeout)
        processed_vehicles = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            limited_links = list(all_links)[:20]  # Limit to prevent timeout
            
            for i, url in enumerate(limited_links):
                try:
                    logger.info(f"Processing vehicle {i+1}/{len(limited_links)}: {url}")
                    response = await client.get(url, headers=HEADERS)
                    if response.status_code == 200:
                        vehicle = extract_vehicle_details(response.text, url)
                        processed_vehicles.append(vehicle.dict())
                        logger.info(f"Successfully processed: {vehicle.title}")
                    await asyncio.sleep(1)  # Be respectful
                except Exception as e:
                    logger.error(f"Error processing details for {url}: {e}")

        vehicles_db = processed_vehicles
        logger.info(f"Processed details for {len(vehicles_db)} vehicles.")
        
        return {
            "message": "Scraping and processing completed successfully.",
            "found": len(all_links),
            "processed": len(vehicles_db),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Scrape process failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scrape failed: {e}")

@app.get("/api/vehicles")
async def get_vehicles():
    logger.info(f"Returning {len(vehicles_db)} vehicles")
    return vehicles_db

@app.post("/api/update-listings")
async def update_listings(request: UpdateRequest):
    logger.info(f"Updating {len(request.urls)} listings...")
    updated_vehicles = []
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in request.urls:
            try:
                response = await client.get(url, headers=HEADERS)
                if response.status_code == 200:
                    details = extract_vehicle_details(response.text, url)
                    updated_vehicles.append(UpdateResponse(
                        url=url,
                        price=details.price,
                        bids=details.bids,
                        time_remaining=details.time_remaining_or_date_sold,
                        status=details.status
                    ).dict())
                await asyncio.sleep(0.5)  # Small delay between requests
            except Exception as e:
                logger.error(f"Failed to update {url}: {e}")

    logger.info(f"Successfully updated {len(updated_vehicles)} listings.")
    return updated_vehicles

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)