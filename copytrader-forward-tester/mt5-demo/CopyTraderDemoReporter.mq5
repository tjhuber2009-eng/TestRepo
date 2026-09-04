#property strict
#property description "Read-only demo-account reporter for CopyTrader validation."
#property version   "1.00"

input string CandidateId = "UNSET";
input int SnapshotSeconds = 60;

datetime g_start_time = 0;
string g_prefix = "";

string CleanToken(string s)
{
   string out="";
   for(int i=0;i<StringLen(s);i++)
   {
      ushort c=StringGetCharacter(s,i);
      if((c>='0' && c<='9') || (c>='A' && c<='Z') || (c>='a' && c<='z') || c=='-' || c=='_')
         out += StringSubstr(s,i,1);
      else
         out += "_";
   }
   return out;
}

string DealTypeName(long t)
{
   switch((ENUM_DEAL_TYPE)t)
   {
      case DEAL_TYPE_BUY: return "BUY";
      case DEAL_TYPE_SELL: return "SELL";
      case DEAL_TYPE_BALANCE: return "BALANCE";
      case DEAL_TYPE_CREDIT: return "CREDIT";
      case DEAL_TYPE_CHARGE: return "CHARGE";
      case DEAL_TYPE_CORRECTION: return "CORRECTION";
      case DEAL_TYPE_BONUS: return "BONUS";
      case DEAL_TYPE_COMMISSION: return "COMMISSION";
      case DEAL_TYPE_COMMISSION_DAILY: return "COMMISSION_DAILY";
      case DEAL_TYPE_COMMISSION_MONTHLY: return "COMMISSION_MONTHLY";
      case DEAL_TYPE_COMMISSION_AGENT_DAILY: return "COMMISSION_AGENT_DAILY";
      case DEAL_TYPE_COMMISSION_AGENT_MONTHLY: return "COMMISSION_AGENT_MONTHLY";
      case DEAL_TYPE_INTEREST: return "INTEREST";
      case DEAL_TYPE_BUY_CANCELED: return "BUY_CANCELED";
      case DEAL_TYPE_SELL_CANCELED: return "SELL_CANCELED";
      case DEAL_TYPE_DIVIDEND: return "DIVIDEND";
      case DEAL_TYPE_DIVIDEND_FRANKED: return "DIVIDEND_FRANKED";
      case DEAL_TYPE_TAX: return "TAX";
   }
   return "OTHER";
}

string EntryName(long e)
{
   switch((ENUM_DEAL_ENTRY)e)
   {
      case DEAL_ENTRY_IN: return "IN";
      case DEAL_ENTRY_OUT: return "OUT";
      case DEAL_ENTRY_INOUT: return "INOUT";
      case DEAL_ENTRY_OUT_BY: return "OUT_BY";
   }
   return "UNKNOWN";
}

int OpenAppendCsv(string name, string header)
{
   int h=FileOpen(name,FILE_COMMON|FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(h==INVALID_HANDLE) return INVALID_HANDLE;
   if(FileSize(h)==0)
      FileWriteString(h,header+"\r\n");
   FileSeek(h,0,SEEK_END);
   return h;
}

void WriteAccountSnapshot()
{
   int h=OpenAppendCsv(g_prefix+"_account.csv",
      "utc,server_time,login,server,candidate,balance,equity,profit,margin,margin_free,margin_level,positions,orders");
   if(h==INVALID_HANDLE) return;
   FileWrite(h,
      TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS),
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
      (long)AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      CandidateId,
      DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),
      DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2),
      DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT),2),
      DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2),
      DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2),
      DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL),2),
      PositionsTotal(),
      OrdersTotal());
   FileClose(h);
}

void WritePositions()
{
   int h=OpenAppendCsv(g_prefix+"_positions.csv",
      "utc,login,candidate,ticket,symbol,type,volume,price_open,price_current,sl,tp,profit,swap,magic,comment");
   if(h==INVALID_HANDLE) return;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      FileWrite(h,
         TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS),
         (long)AccountInfoInteger(ACCOUNT_LOGIN),
         CandidateId,
         (long)ticket,
         PositionGetString(POSITION_SYMBOL),
         (long)PositionGetInteger(POSITION_TYPE),
         DoubleToString(PositionGetDouble(POSITION_VOLUME),4),
         DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),8),
         DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT),8),
         DoubleToString(PositionGetDouble(POSITION_SL),8),
         DoubleToString(PositionGetDouble(POSITION_TP),8),
         DoubleToString(PositionGetDouble(POSITION_PROFIT),2),
         DoubleToString(PositionGetDouble(POSITION_SWAP),2),
         (long)PositionGetInteger(POSITION_MAGIC),
         PositionGetString(POSITION_COMMENT));
   }
   FileClose(h);
}

void WriteDeals()
{
   if(!HistorySelect(g_start_time,TimeCurrent())) return;
   int h=OpenAppendCsv(g_prefix+"_deals.csv",
      "utc,login,candidate,ticket,order,position_id,symbol,type,type_name,entry,entry_name,volume,price,profit,commission,swap,fee,magic,comment");
   if(h==INVALID_HANDLE) return;

   int total=HistoryDealsTotal();
   for(int i=0;i<total;i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0) continue;
      datetime dt=(datetime)HistoryDealGetInteger(ticket,DEAL_TIME);
      if(dt<g_start_time) continue;
      long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      FileWrite(h,
         TimeToString(dt,TIME_DATE|TIME_SECONDS),
         (long)AccountInfoInteger(ACCOUNT_LOGIN),
         CandidateId,
         (long)ticket,
         (long)HistoryDealGetInteger(ticket,DEAL_ORDER),
         (long)HistoryDealGetInteger(ticket,DEAL_POSITION_ID),
         HistoryDealGetString(ticket,DEAL_SYMBOL),
         type,
         DealTypeName(type),
         entry,
         EntryName(entry),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_VOLUME),4),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_PRICE),8),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_PROFIT),2),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_COMMISSION),2),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_SWAP),2),
         DoubleToString(HistoryDealGetDouble(ticket,DEAL_FEE),2),
         (long)HistoryDealGetInteger(ticket,DEAL_MAGIC),
         HistoryDealGetString(ticket,DEAL_COMMENT));
   }
   FileClose(h);
}

void Capture()
{
   WriteAccountSnapshot();
   WritePositions();
   WriteDeals();
}

int OnInit()
{
   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("COPYTRADER DEMO REPORTER REFUSED: this account is not DEMO.");
      return(INIT_FAILED);
   }

   if(CandidateId=="UNSET" || StringLen(CandidateId)<2)
   {
      Print("Set CandidateId before starting.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   long login=AccountInfoInteger(ACCOUNT_LOGIN);
   g_prefix="COPYTRADER_DEMO_"+IntegerToString((int)login)+"_"+CleanToken(CandidateId);

   string key="COPYTRADER_START_"+IntegerToString((int)login)+"_"+CleanToken(CandidateId);
   if(GlobalVariableCheck(key))
      g_start_time=(datetime)GlobalVariableGet(key);
   else
   {
      g_start_time=TimeCurrent();
      GlobalVariableSet(key,(double)g_start_time);
   }

   EventSetTimer(MathMax(10,SnapshotSeconds));
   Capture();
   Print("CopyTraderDemoReporter started on DEMO account ",login,
         " candidate=",CandidateId," start=",TimeToString(g_start_time,TIME_DATE|TIME_SECONDS));
   return(INIT_SUCCEEDED);
}

void OnTimer()
{
   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("Account is no longer DEMO. Reporter stopping.");
      ExpertRemove();
      return;
   }
   Capture();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}
