from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
import asyncio
from bs4 import BeautifulSoup
import re
import logging
from datetime import datetime
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Grays Auction Scraper", version="1.0.0")

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
    features: dict = {}

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
    """Extract vehicle links from Grays search page"""
    soup = BeautifulSoup(html_content, 'html.parser')
    links = set()
    
    for link in soup.find_all('a', href__=True):
        href = link.get('href')
        if href and re.search(r'/lot/\d+', href):
            text = link.get_text(strip=True)
            if re.match(r'^\d{4}\b', text) and 'motorbike' not in text.lower():
                full_url = href if href.startswith('http') else f"https://www.grays.com{href}"
                links.add(full_url)
    
    return list(links)

def extract_vehicle_details(html_content: str, url: str) -> Vehicle:
    """Extract detailed vehicle information from individual page"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Extract title
    title_elem = soup.find('h1')
    title = title_elem.get_text(strip=True) if title_elem else "Unknown Vehicle"
    title_parts = title.split()
    
    def get_text_by_label(label: str) -> Optional[str]:
        for elem in soup.find_all(['li', 'div', 'span', 'td', 'th']):
            text = elem.get_text().lower()
            if label.lower() in text:
                match = re.search(rf'{label}:?\s*(.+)', elem.get_text(), re.IGNORECASE)
                return match.group(1).strip() if match else None
        return None
    
    # Extract current price and bids
    price_elem = soup.find("span", attrs={"itemprop": "price"})
    price = None
    if price_elem:
        price_text = re.sub(r'[^\d.]', '', price_elem.get_text())
        price = float(price_text) if price_text else None
    
    # Extract bids
    bids = 0
    bid_link = soup.find("a", string=re.compile(r"\d+\s+bids?", re.IGNORECASE))
    if bid_link:
        bid_match = re.search(r'(\d+)', bid_link.get_text())
        bids = int(bid_match.group(1)) if bid_match else 0
    
    # Extract time remaining
    time_elem = soup.find("span", id="lot-closing-countdown")
    time_remaining = time_elem.get_text(strip=True) if time_elem else "Auction Ended"
    
    # Determine status
    status = "active"
    if time_remaining == "Auction Ended" or not re.search(r'\d', time_remaining):
        status = "sold" if price and bids > 0 else "referred"
    
    return Vehicle(
        url=url,
        title=title,
        make=title_parts[1] if len(title_parts) > 1 else "Unknown",
        model=title_parts[2] if len(title_parts) > 2 else "Unknown", 
        year=int(title_parts[0]) if title_parts and title_parts[0].isdigit() else None,
        variant=" ".join(title_parts[3:]) if len(title_parts) > 3 else "",
        body_type=get_text_by_label("Body Type") or "",
        fuel_type=get_text_by_label("Fuel Type") or "",
        transmission=get_text_by_label("Transmission") or "",
        exterior_colour=get_text_by_label("Colour") or "",
        location=get_text_by_label("Location") or "",
        price=price,
        bids=bids,
        time_remaining_or_date_sold=time_remaining,
        status=status,
        features={}
    )

@app.get("/")
async def root():
    return {"message": "Grays Auction Scraper API", "status": "running"}

@app.post("/scrape")  # Changed from /api/scrape
async def trigger_scrape():
    try:
        logger.info("Starting scrape process...")
        
        # Clear existing data
        vehicles_db.clear()
        
        base_url = "https://www.grays.com/search/automotive-trucks-and-marine/motor-vehiclesmotor-cycles/motor-vehicles"
        all_links = set()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for page in range(1, 6):  # Scrape first 5 pages
                try:
                    url = f"{base_url}?tab=items&isdesktop=1&page={page}"
                    response = await client.get(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    
                    if response.status_code == 200:
                        links = extract_vehicle_links(response.text)
                        all_links.update(links)
                        logger.info(f"Page {page}: Found {len(links)} links")
                    
                    await asyncio.sleep(2)  # Rate limiting
                    
                except Exception as e:
                    logger.error(f"Error on page {page}: {e}")
        
        logger.info(f"Total links found: {len(all_links)}")
        
        # Process each vehicle link
        processed = 0
        async with httpx.AsyncClient(timeout=30.0) as client:
            for url in list(all_links)[:50]:  # Limit to first 50 for testing
                try:
                    response = await client.get(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    
                    if response.status_code == 200:
                        vehicle = extract_vehicle_details(response.text, url)
                        vehicles_db.append(vehicle.dict())
                        processed += 1
                    
                    await asyncio.sleep(1)  # Rate limiting
                    
                except Exception as e:
                    logger.error(f"Error processing {url}: {e}")
        
        logger.info(f"Processed {processed} vehicles")
        
        return {
            "message": f"Scraping completed successfully",
            "found": len(all_links),
            "processed": processed,
            "count": len(vehicles_db)
        }
        
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/vehicles")  # Changed from /api/vehicles
async def get_vehicles():
    return vehicles_db

@app.post("/update-listings")  # Changed from /api/update-listings
async def update_listings(request: UpdateRequest):

    """Update specific vehicle listings with current price/bid data"""
    updates = []
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in request.urls[:20]:  # Limit to 20 for performance
            try:
                response = await client.get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Extract current price
                    price_elem = soup.find("span", attrs={"itemprop": "price"})
                    price = None
                    if price_elem:
                        price_text = re.sub(r'[^\d.]', '', price_elem.get_text())
                        price = float(price_text) if price_text else None
                    
                    # Extract bids
                    bids = 0
                    bid_link = soup.find("a", string=re.compile(r"\d+\s+bids?", re.IGNORECASE))
                    if bid_link:
                        bid_match = re.search(r'(\d+)', bid_link.get_text())
                        bids = int(bid_match.group(1)) if bid_match else 0
                    
                    # Extract time remaining
                    time_elem = soup.find("span", id="lot-closing-countdown")
                    time_remaining = time_elem.get_text(strip=True) if time_elem else "Auction Ended"
                    
                    # Determine status
                    status = "active"
                    if time_remaining == "Auction Ended" or not re.search(r'\d', time_remaining):
                        status = "sold" if price and bids > 0 else "referred"
                    
                    updates.append({
                        "url": url,
                        "price": price,
                        "bids": bids,
                        "time_remaining": time_remaining,
                        "status": status
                    })
                
                await asyncio.sleep(1)  # Be respectful
                
            except Exception as e:
                logger.error(f"Error updating {url}: {str(e)}")
                continue
    
    return updates

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)