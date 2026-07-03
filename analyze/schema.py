"""The decision-grade record the LLM must emit per post.
Field descriptions ARE part of the prompt: google-genai passes them to the model
as the response schema, so keep them tight and instructive."""
from enum import Enum
from typing import List
from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


class Emotion(str, Enum):
    anger = "anger"
    frustration = "frustration"
    fear = "fear"
    disappointment = "disappointment"
    joy = "joy"
    gratitude = "gratitude"
    neutral = "neutral"
    other = "other"


class Urgency(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Intent(str, Enum):
    complaint = "complaint"
    query = "query"
    praise = "praise"
    churn_threat = "churn_threat"
    legal_threat = "legal_threat"
    fraud_report = "fraud_report"
    journalist_or_influencer = "journalist_or_influencer"
    suggestion = "suggestion"
    spam = "spam"
    other = "other"


class Aspect(str, Enum):
    mobile_app = "mobile_app"
    internet_banking = "internet_banking"
    upi_payments = "upi_payments"
    cards = "cards"
    loans = "loans"
    accounts = "accounts"
    branch_atm = "branch_atm"
    customer_support = "customer_support"
    fees_charges = "fees_charges"
    fraud_security = "fraud_security"
    other = "other"


class Team(str, Enum):
    app_engineering = "app_engineering"
    cards = "cards"
    payments_upi = "payments_upi"
    loans = "loans"
    branch_ops = "branch_ops"
    customer_support = "customer_support"
    fraud_cyber = "fraud_cyber"
    retention = "retention"
    comms_pr = "comms_pr"
    none = "none"


class RBICategory(str, Enum):
    atm_debit_card = "atm_debit_card"
    credit_card = "credit_card"
    mobile_internet_banking = "mobile_internet_banking"
    upi = "upi"
    loans_advances = "loans_advances"
    deposit_accounts = "deposit_accounts"
    mis_selling = "mis_selling"
    levy_of_charges = "levy_of_charges"
    other = "other"
    not_applicable = "not_applicable"


class AspectSentiment(BaseModel):
    aspect: Aspect = Field(description="Which banking area this opinion is about")
    sentiment: Sentiment = Field(description="Sentiment toward THIS aspect specifically")
    evidence: str = Field(description="Short quoted span from the post that justifies it")


class PostAnalysis(BaseModel):
    source_id: str = Field(description="Echo the source_id from the input EXACTLY")
    sentiment: Sentiment = Field(description="Overall sentiment of the whole post")
    score: float = Field(description="Overall sentiment from -1.0 (very negative) to +1.0 (very positive)")
    emotion: Emotion = Field(description="Dominant emotion")
    emotion_intensity: int = Field(description="Emotion strength 1 (mild) to 5 (extreme)")
    sarcasm: bool = Field(description="True if the post is sarcastic/ironic (praise-shaped but negative)")
    aspects: List[AspectSentiment] = Field(description="One entry per banking area mentioned")
    intent: Intent = Field(description="Primary intent of the author")
    urgency: Urgency = Field(description="critical=money stuck/fraud/viral; high=blocked service; medium=annoyance; low=chatter")
    urgency_reason: str = Field(description="One short phrase justifying the urgency")
    product: str = Field(description="Named product if any (e.g. Axis Magnus, Axis Mobile, ASAP); else 'unspecified'")
    root_cause: str = Field(description="The concrete underlying problem in <=12 words; '' if none")
    rbi_category: RBICategory = Field(description="RBI grievance category if it's a complaint, else not_applicable")
    recommended_team: Team = Field(description="Team that should own this")
    recommended_action: str = Field(description="Next best action in <=12 words")
    churn_risk: bool = Field(description="True if the author threatens to leave/close account")
    fraud_signal: bool = Field(description="True if impersonation, phishing, scam narrative, or fraud report")
    fraud_type: str = Field(description="impersonation/phishing/mule/scam-report/none")
    pii_present: bool = Field(description="True if the post contains account/card/phone/personal identifiers")
    theme: str = Field(description="Short reusable theme label for clustering, e.g. 'UPI failure after app update'")
    summary: str = Field(description="One-line neutral summary of the post")
    confidence: float = Field(description="Your confidence in this analysis, 0.0 to 1.0")
