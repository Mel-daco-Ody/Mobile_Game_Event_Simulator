# UA Spend Dataset (Bước 24)

Dataset này chứa chi tiêu User Acquisition synthetic theo schema:

```
date,platform,country,source,campaign,spend,impressions,clicks,installs
2026-09-01,android,VN,facebook,VN_AUG_01,500.00,120000,4200,850
```

## Cách sử dụng

```python
from simulation.ua_spend import create_ua_spend_generator
from datetime import date

gen = create_ua_spend_generator(rng_seed=42)
records = gen.generate(
    start_date=date(2026, 9, 1),
    n_days=30,
)
gen.save_csv(records, "data/ua_spend/ua_spend.csv")
```

Hoặc chạy script:

```bash
python simulation/generate_ua_spend.py --days 30 --output data/ua_spend/ua_spend.csv
```

## Metrics có thể tính

- **CPI** = spend / installs
- **CTR** = clicks / impressions
- **CVR** = installs / clicks
- **ROAS** = revenue / spend (cần kết hợp với business events)
- **CAC** = spend / installs
- **LTV:CAC** = ltv / cac (target ≥ 3.0)
- **Payback** = cac / arpu_per_month

## File `ua_spend.csv`

Sẽ được sinh ra khi chạy generate script. KHÔNG commit file này
nếu muốn giữ repo gọn (chỉ commit các file metadata + sample).
