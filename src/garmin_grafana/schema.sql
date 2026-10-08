CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS garmin;

CREATE TABLE IF NOT EXISTS garmin.responses (
    endpoint text NOT NULL,
    key text NOT NULL,
    digest text NOT NULL,
    payload jsonb NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (endpoint, key, digest)
);
CREATE TABLE IF NOT EXISTS garmin.files (
    key text NOT NULL,
    digest text NOT NULL,
    content bytea NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (key, digest)
);
CREATE TABLE IF NOT EXISTS garmin.state (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS garmin.samples (
    source text NOT NULL,
    measurement text NOT NULL,
    time timestamptz NOT NULL,
    entity text NOT NULL DEFAULT '',
    fields jsonb NOT NULL,
    PRIMARY KEY (source, measurement, time, entity)
);
SELECT create_hypertable('garmin.samples', by_range('time', INTERVAL '30 days'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS samples_measurement_time ON garmin.samples (measurement, time DESC);
CREATE INDEX IF NOT EXISTS samples_measurement_entity_time ON garmin.samples (measurement, entity, time);

CREATE OR REPLACE VIEW garmin."DeviceSync" AS
SELECT time FROM garmin.samples WHERE measurement = 'DeviceSync';

CREATE OR REPLACE VIEW garmin."ActivityGPS" AS
SELECT time,
       entity AS "ActivityID",
       (fields->>'Altitude')::double precision AS "Altitude",
       (fields->>'Cadence')::double precision AS "Cadence",
       (fields->>'DurationSeconds')::double precision AS "DurationSeconds",
       (fields->>'Fractional_Cadence')::double precision AS "Fractional_Cadence",
       (fields->>'HeartRate')::double precision AS "HeartRate",
       (fields->>'Latitude')::double precision AS "Latitude",
       (fields->>'Longitude')::double precision AS "Longitude",
       (fields->>'Power')::double precision AS "Power",
       (fields->>'Speed')::double precision AS "Speed",
       (fields->>'Stance_Time')::double precision AS "Stance_Time",
       (fields->>'Vertical_Oscillation')::double precision AS "Vertical_Oscillation",
       (fields->>'Step_Length')::double precision AS "Step_Length"
FROM garmin.samples WHERE measurement = 'ActivityGPS';

CREATE OR REPLACE VIEW garmin."ActivityIndex" AS
SELECT time,
       entity AS "ActivityID",
       fields->>'Label' AS "Label"
FROM garmin.samples WHERE measurement = 'ActivityIndex';

CREATE OR REPLACE VIEW garmin."ActivityLap" AS
SELECT time,
       entity AS "ActivityID",
       (fields->>'Ascent')::double precision AS "Ascent",
       (fields->>'Avg_HR')::double precision AS "Avg_HR",
       (fields->>'Descent')::double precision AS "Descent",
       (fields->>'Distance')::double precision AS "Distance",
       (fields->>'Elapsed_Time')::double precision AS "Elapsed_Time",
       (fields->>'Index')::double precision AS "Index",
       (fields->>'Max_HR')::double precision AS "Max_HR"
FROM garmin.samples WHERE measurement = 'ActivityLap';

CREATE OR REPLACE VIEW garmin."ActivitySummary" AS
SELECT time,
       entity AS "ActivityID",
       fields->>'activityType' AS "activityType",
       (fields->>'aerobicTrainingEffect')::double precision AS "aerobicTrainingEffect",
       (fields->>'averageHR')::double precision AS "averageHR",
       (fields->>'averageSpeed')::double precision AS "averageSpeed",
       (fields->>'calories')::double precision AS "calories",
       (fields->>'distance')::double precision AS "distance",
       (fields->>'elapsedDuration')::double precision AS "elapsedDuration",
       (fields->>'elevationGain')::double precision AS "elevationGain",
       (fields->>'hrZoneLowBoundary_1')::double precision AS "hrZoneLowBoundary_1",
       (fields->>'hrZoneLowBoundary_2')::double precision AS "hrZoneLowBoundary_2",
       (fields->>'hrZoneLowBoundary_3')::double precision AS "hrZoneLowBoundary_3",
       (fields->>'hrZoneLowBoundary_4')::double precision AS "hrZoneLowBoundary_4",
       (fields->>'hrZoneLowBoundary_5')::double precision AS "hrZoneLowBoundary_5",
       (fields->>'maxHR')::double precision AS "maxHR"
FROM garmin.samples WHERE measurement = 'ActivitySummary';

CREATE OR REPLACE VIEW garmin."ActivityWeather" AS
SELECT time,
       entity AS "ActivityID",
       (fields->>'FeelsLike')::double precision AS "FeelsLike",
       (fields->>'Humidity')::double precision AS "Humidity",
       (fields->>'Temperature')::double precision AS "Temperature",
       fields->>'Wind' AS "Wind"
FROM garmin.samples WHERE measurement = 'ActivityWeather';

CREATE OR REPLACE VIEW garmin.efforts AS
SELECT time,
       split_part(entity, ':', 1) AS "ActivityID",
       fields->>'ActivityName' AS "ActivityName",
       (fields->>'CoveredMeters')::double precision AS "CoveredMeters",
       fields->>'Date' AS "Date",
       fields->>'Distance' AS "Distance",
       fields->>'Duration' AS "Duration",
       (fields->>'Meters')::double precision AS "Meters",
       fields->>'Pace' AS "Pace",
       (fields->>'Seconds')::double precision AS "Seconds"
FROM garmin.samples WHERE measurement = 'BestEffort';

CREATE OR REPLACE VIEW garmin."BodyBatteryIntraday" AS
SELECT time,
       (fields->>'BodyBatteryLevel')::double precision AS "BodyBatteryLevel"
FROM garmin.samples WHERE measurement = 'BodyBatteryIntraday';

CREATE OR REPLACE VIEW garmin."BodyComposition" AS
SELECT time,
       (fields->>'weight')::double precision AS "weight"
FROM garmin.samples WHERE measurement = 'BodyComposition';

CREATE OR REPLACE VIEW garmin."BreathingRateIntraday" AS
SELECT time,
       (fields->>'BreathingRate')::double precision AS "BreathingRate"
FROM garmin.samples WHERE measurement = 'BreathingRateIntraday';

CREATE OR REPLACE VIEW garmin."DailyStats" AS
SELECT time,
       (fields->>'activeKilocalories')::double precision AS "activeKilocalories",
       (fields->>'activeSeconds')::double precision AS "activeSeconds",
       (fields->>'averageSpo2')::double precision AS "averageSpo2",
       (fields->>'bmrKilocalories')::double precision AS "bmrKilocalories",
       (fields->>'bodyBatteryChargedValue')::double precision AS "bodyBatteryChargedValue",
       (fields->>'bodyBatteryDrainedValue')::double precision AS "bodyBatteryDrainedValue",
       (fields->>'highStressDuration')::double precision AS "highStressDuration",
       (fields->>'highlyActiveSeconds')::double precision AS "highlyActiveSeconds",
       (fields->>'lowStressDuration')::double precision AS "lowStressDuration",
       (fields->>'mediumStressDuration')::double precision AS "mediumStressDuration",
       (fields->>'moderateIntensityMinutes')::double precision AS "moderateIntensityMinutes",
       (fields->>'restStressDuration')::double precision AS "restStressDuration",
       (fields->>'restingHeartRate')::double precision AS "restingHeartRate",
       (fields->>'sedentarySeconds')::double precision AS "sedentarySeconds",
       (fields->>'sleepingSeconds')::double precision AS "sleepingSeconds",
       (fields->>'totalDistanceMeters')::double precision AS "totalDistanceMeters",
       (fields->>'totalSteps')::double precision AS "totalSteps",
       (fields->>'uncategorizedStressDuration')::double precision AS "uncategorizedStressDuration",
       (fields->>'vigorousIntensityMinutes')::double precision AS "vigorousIntensityMinutes"
FROM garmin.samples WHERE measurement = 'DailyStats';

CREATE OR REPLACE VIEW garmin."HRV_Intraday" AS
SELECT time,
       (fields->>'hrvValue')::double precision AS "hrvValue"
FROM garmin.samples WHERE measurement = 'HRV_Intraday';

CREATE OR REPLACE VIEW garmin."HeartRateIntraday" AS
SELECT time,
       (fields->>'HeartRate')::double precision AS "HeartRate"
FROM garmin.samples WHERE measurement = 'HeartRateIntraday';

CREATE OR REPLACE VIEW garmin."RacePredictions" AS
SELECT time,
       (fields->>'time10K')::double precision AS "time10K",
       (fields->>'time5K')::double precision AS "time5K",
       (fields->>'timeHalfMarathon')::double precision AS "timeHalfMarathon",
       (fields->>'timeMarathon')::double precision AS "timeMarathon"
FROM garmin.samples WHERE measurement = 'RacePredictions';

CREATE OR REPLACE VIEW garmin."SleepStage" AS
SELECT time,
       (fields->>'Clock')::double precision AS "Clock",
       (fields->>'Day')::double precision AS "Day",
       (fields->>'Stage')::double precision AS "Stage"
FROM garmin.samples WHERE measurement = 'SleepStage';

CREATE OR REPLACE VIEW garmin."SleepSummary" AS
SELECT time,
       (fields->>'averageSpO2Value')::double precision AS "averageSpO2Value",
       (fields->>'avgSleepStress')::double precision AS "avgSleepStress",
       (fields->>'awakeCount')::double precision AS "awakeCount",
       (fields->>'awakeSleepSeconds')::double precision AS "awakeSleepSeconds",
       (fields->>'deepSleepSeconds')::double precision AS "deepSleepSeconds",
       (fields->>'lightSleepSeconds')::double precision AS "lightSleepSeconds",
       (fields->>'remSleepSeconds')::double precision AS "remSleepSeconds",
       (fields->>'restlessMomentsCount')::double precision AS "restlessMomentsCount",
       (fields->>'sleepScore')::double precision AS "sleepScore",
       (fields->>'sleepTimeSeconds')::double precision AS "sleepTimeSeconds"
FROM garmin.samples WHERE measurement = 'SleepSummary';

CREATE OR REPLACE VIEW garmin."StepRecord" AS
SELECT time,
       (fields->>'Order')::double precision AS "Order",
       (fields->>'Steps')::double precision AS "Steps",
       fields->>'Title' AS "Title"
FROM garmin.samples WHERE measurement = 'StepRecord';

CREATE OR REPLACE VIEW garmin."StressIntraday" AS
SELECT time,
       (fields->>'stressLevel')::double precision AS "stressLevel"
FROM garmin.samples WHERE measurement = 'StressIntraday';

CREATE OR REPLACE VIEW garmin."VO2_Max" AS
SELECT time,
       (fields->>'VO2_max_value')::double precision AS "VO2_max_value"
FROM garmin.samples WHERE measurement = 'VO2_Max';

CREATE OR REPLACE VIEW garmin."BestEffort" AS
SELECT *,
       min("Seconds") OVER (PARTITION BY "Distance" ORDER BY time, "ActivityID" ROWS UNBOUNDED PRECEDING) AS "Best",
       CASE WHEN "Seconds" < coalesce(min("Seconds") OVER (
           PARTITION BY "Distance" ORDER BY time, "ActivityID" ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
       ), 'Infinity'::double precision) THEN 1 ELSE 0 END AS "Record"
FROM garmin.efforts;
