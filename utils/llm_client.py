from groq import Groq
import os

# Load environment variables
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Initialize Groq client
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
else:
    groq_client = None
    print("GROQ_API_KEY not set. LLM client will not function.")

async def analyze_with_llm(prompt: str) -> str:
    """
    Sends a prompt to the Groq API and returns the LLM's response.
    """
    if not groq_client:
        return "LLM client not initialized. Cannot generate analysis."

    try:
        print(f"Sending prompt to Groq API: {prompt[:100]}...") # Log first 100 chars of prompt
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            # Model selection - Llama 3 is recommended and fast
            model="llama3-70b-8192", 
            # Other potential models: "llama3-8b-8192", "mixtral-8x7b-32768", "gemma-7b-it"
        )
        response_text = chat_completion.choices[0].message.content
        print("Received response from Groq API.")
        return response_text
    except Exception as e:
        print(f"Error calling Groq API: {e}")
        return f"Error during LLM analysis: {e}"

# Example prompt structure for trade analysis:
# def create_trade_analysis_prompt(
#     ticker: str,
#     signal_details: str, # e.g., "Volume Breakthrough: 300% above average"
#     fundamental_data: dict, # Parsed fundamentals from screener API
#     theme_data: list, # Parsed themes from screener API
#     technical_summary: str # Parsed technicals from screener API
# ) -> str:
#     prompt = f"""
#     Analyze the following stock for a potential trading opportunity. Provide concise pros and cons for a day trade.
#     Focus on information relevant to identifying short-term (day trading) opportunities.
#     
#     Stock Ticker: {ticker}
#     Signal: {signal_details}
#     
#     Fundamental Data:
#     Revenue Growth: {fundamental_data.get("revenueGrowth", "N/A")}
#     EPS Growth: {fundamental_data.get("epsGrowth", "N/A")}
#     Market Cap: {fundamental_data.get("marketCap", "N/A")}
#     (Add other relevant fundamentals)
#     
#     Themes:
#     """
#     for theme in theme_data:
#         prompt += f"- {theme}\n"
#     
#     prompt += f"\nTechnical Summary: {technical_summary}\n\nProvide the analysis in the following JSON format:\n{{\n  \"pros\": \"[list of pros]\",\n  \"cons\": \"[list of cons]\"
# }}
#     """
#     return prompt
