import logging
from groq import Groq
from langsmith import traceable
from config import Config

logger = logging.getLogger(__name__)

class LlmClient:
    def __init__(self):
        self.client = Groq(api_key=Config.GROQ_API_KEY)

    @traceable(run_type="llm")
    def analyze_stock(self, ticker, signal_details, stock_data):
        """Generates a Pros/Cons summary for a trade setup."""
        prompt = (
            f"Analyze the following stock trade signal for {ticker}:\n"
            f"Signal Trigger: {signal_details}\n"
            f"Stock Context: {stock_data}\n\n"
            "Provide a concise summary with 'Pros' and 'Cons' for this trade. "
            "Keep it under 150 words."
        )
        
        try:
            completion = self.client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "You are a professional trading analyst."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=256
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM analysis failed for {ticker}: {e}")
            return "Unable to generate AI analysis at this time."
