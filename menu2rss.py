import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import locale
from feedgen.feed import FeedGenerator

# Set the locale to Dutch
locale.setlocale(locale.LC_TIME, 'nl_NL.UTF-8')

# Get the current date and format it as "day month year" (e.g., "29 januari 2024")
current_date = datetime.now().strftime("%d %B %Y").lower()

# Function to check if a given date is a Monday, Tuesday, Thursday, or Friday
def is_valid_day(date):
    return date.weekday() in [0, 1, 3, 4]

# The URL of the page
url = "https://order.hanssens.be/menu/C32A/GBS-Melle-De-Parkschool"

# Send a GET request to the URL
response = requests.get(url)

# Check if the request was successful (status code 200)
if response.status_code == 200:
    # Parse the HTML content of the page
    soup = BeautifulSoup(response.content, 'html.parser')

    # Create RSS feed
    fg = FeedGenerator()
    fg.title('Weekmenu')
    fg.link(href=url, rel='alternate')
    fg.description('Weekmenu De Parkschool Melle')

    # Get current date
    current_date_obj = datetime.now()

    # Iterate over the next 5 dates
    for i in range(5):
        next_date = current_date_obj + timedelta(days=i)
        if is_valid_day(next_date):
            # Find the day__content--items for the current day
            day_headers = soup.find_all("div", class_="day__header--day")
            for day_header in day_headers:
                date_text = day_header.find_next("div", class_="day__header--date").text.lower()
                header_date = datetime.strptime(date_text, "%d %B %Y")

                if header_date.date() == next_date.date():
                    current_day_items_menu = day_header.find_next("div", class_="day__content--menu").find_all("div", class_="day__content--item")
                    current_day_items_soup = day_header.find_next("div", class_="day__content--soup").find_all("div", class_="day__content--item")

                    # Add items to RSS feed
                    fe = fg.add_entry()
                    fe.title(next_date.strftime("%A, %d %B %Y"))
                    fe.link(href=url)
                    description = ""
                    for item in current_day_items_soup:
                        description += item.text + "\n"
                    for item in current_day_items_menu:
                        description += item.text + "\n"
                    fe.description(description)

                    break

    # Generate the RSS feed
    rss_feed = fg.rss_str(pretty=True)
    
    # Save the RSS output to a file
    with open("menu_feed.xml", "w", encoding="utf-8") as file:
        file.write(rss_feed.decode('utf-8'))
        
    print("RSS feed saved as menu_feed.xml")
else:
    print("/")
