from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
import asyncio
from bs4 import BeautifulSoup
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Grays Auction Scraper", version="1.0.0")

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
    features: dict = {}

class UpdateRequest(BaseModel):
    urls: List[str]

class UpdateResponse(BaseModel):
    url: str
    price: Optional[float]
    bids: int
    time_remaining: str
    status: str

vehicles_db = []

def extract_vehicle_links(html_content: str) -> List[str]:
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
    soup = BeautifulSoup(html_content, 'html.parser')
    
    title_elem = soup.find('h1')
    title = title_elem.get_text(strip=True) if title_elem else "Unknown Vehicle"
    title_parts = title.split()
    
    # Extract price
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
    
    # Status
    status = "active"
    if time_remaining == "Auction Ended" or not re.search(r'\d', time_remaining):
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
        features={}
    )

@app.get("/")
async def root():
    return {"message": "Grays Auction Scraper API", "status": "running"}

@app.get("/api/vehicles", response_model=List[Vehicle])
async def get_vehicles():
    return vehicles_db

@app.post("/api/scrape")
async def scrape_vehicles(pages: int = 5):
    global vehicles_db
    vehicles_db.clear()
    
    base_url = "https://www.grays.com/search/automotive-trucks-and-marine/motor-vehiclesmotor-cycles/motor-vehicles"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        all_links = set()
        
        for page in range(1, pages + 1):
            try:
                logger.info(f"Scraping page {page}")
                response = await client.get(
                    f"{base_url}?tab=items&isdesktop=1&page={page}",
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                response.raise_for_status()
                
                links = extract_vehicle_links(response.text)
                all_links.update(links)
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error scraping page {page}: {e}")
                continue
        
        logger.info(f"Found {len(all_links)} vehicle links")
        
        for i, url in enumerate(all_links):
            try:
                logger.info(f"Scraping vehicle {i+1}/{len(all_links)}")
                response = await client.get(
                    url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                response.raise_for_status()
                
                vehicle = extract_vehicle_details(response.text, url)
                vehicles_db.append(vehicle)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error scraping vehicle {url}: {e}")
                continue
    
    return {"message": f"Scraped {len(vehicles_db)} vehicles", "count": len(vehicles_db)}

@app.post("/api/update-listings", response_model=List[UpdateResponse])
async def update_listings(request: UpdateRequest):
    updates = []
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in request.urls:
            try:
                response = await client.get(
                    url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract price
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
                
                # Status
                status = "active"
                if time_remaining == "Auction Ended" or not re.search(r'\d', time_remaining):
                    status = "sold" if price and bids > 0 else "referred"
                
                updates.append(UpdateResponse(
                    url=url,
                    price=price,
                    bids=bids,
                    time_remaining=time_remaining,
                    status=status
                ))
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error updating {url}: {e}")
                updates.append(UpdateResponse(
                    url=url,
                    price=None,
                    bids=0,
                    time_remaining="Error",
                    status="error"
                ))
    
    return updates

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)