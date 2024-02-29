import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import locale

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

    # Create HTML content
    html_content = "<!DOCTYPE html><html><head><title>Menu for the week</title><link rel='stylesheet' type='text/css' href='style.css'></head><body>"

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

                    # Add date as heading
                    html_content += f"<h1>{next_date.strftime('%A, %d %B %Y')}</h1>"

                    # Add menu items
                    html_content += "<ul>"
                    for item in current_day_items_soup:
                        html_content += f"<li><h2>{item.text}</h2></li>"
                    for item in current_day_items_menu:
                        html_content += f"<li><h2>{item.text}</h2></li>"
                    html_content += "</ul>"

                    break

    # Close HTML content
    html_content += "</body></html>"

    # Save the HTML output to a file
    with open("index.html", "w", encoding="utf-8") as file:
        file.write(html_content)
        
    print("HTML file saved as menu.html")
else:
    print("/")
