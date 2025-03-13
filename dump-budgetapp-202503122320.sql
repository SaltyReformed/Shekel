--
-- PostgreSQL database cluster dump
--

-- Started on 2025-03-12 23:20:21 EDT

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE grubb;
ALTER ROLE grubb WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;

--
-- User Configurations
--








--
-- Databases
--

--
-- Database "template1" dump
--

\connect template1

--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4 (Debian 17.4-1.pgdg120+2)
-- Dumped by pg_dump version 17.2

-- Started on 2025-03-12 23:20:21 EDT

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

-- Completed on 2025-03-12 23:20:21 EDT

--
-- PostgreSQL database dump complete
--

--
-- Database "budgetapp" dump
--

--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4 (Debian 17.4-1.pgdg120+2)
-- Dumped by pg_dump version 17.2

-- Started on 2025-03-12 23:20:21 EDT

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 3600 (class 1262 OID 16384)
-- Name: budgetapp; Type: DATABASE; Schema: -; Owner: grubb
--

CREATE DATABASE budgetapp WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE budgetapp OWNER TO grubb;

\connect budgetapp

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 250 (class 1259 OID 16620)
-- Name: account_interest; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.account_interest (
    id integer NOT NULL,
    account_id integer NOT NULL,
    rate numeric(5,2) NOT NULL,
    compound_frequency character varying(20) DEFAULT 'monthly'::character varying NOT NULL,
    accrual_day integer,
    interest_type character varying(20) DEFAULT 'simple'::character varying,
    enabled boolean DEFAULT true,
    last_accrual_date date
);


ALTER TABLE public.account_interest OWNER TO grubb;

--
-- TOC entry 249 (class 1259 OID 16619)
-- Name: account_interest_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.account_interest_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.account_interest_id_seq OWNER TO grubb;

--
-- TOC entry 3601 (class 0 OID 0)
-- Dependencies: 249
-- Name: account_interest_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.account_interest_id_seq OWNED BY public.account_interest.id;


--
-- TOC entry 220 (class 1259 OID 16401)
-- Name: account_types; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.account_types (
    id integer NOT NULL,
    type_name character varying(50) NOT NULL,
    is_debt boolean NOT NULL
);


ALTER TABLE public.account_types OWNER TO grubb;

--
-- TOC entry 219 (class 1259 OID 16400)
-- Name: account_types_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.account_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.account_types_id_seq OWNER TO grubb;

--
-- TOC entry 3602 (class 0 OID 0)
-- Dependencies: 219
-- Name: account_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.account_types_id_seq OWNED BY public.account_types.id;


--
-- TOC entry 232 (class 1259 OID 16464)
-- Name: accounts; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.accounts (
    id integer NOT NULL,
    user_id integer NOT NULL,
    account_name character varying(100),
    type_id integer,
    balance numeric(10,2)
);


ALTER TABLE public.accounts OWNER TO grubb;

--
-- TOC entry 231 (class 1259 OID 16463)
-- Name: accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.accounts_id_seq OWNER TO grubb;

--
-- TOC entry 3603 (class 0 OID 0)
-- Dependencies: 231
-- Name: accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.accounts_id_seq OWNED BY public.accounts.id;


--
-- TOC entry 228 (class 1259 OID 16439)
-- Name: expense_categories; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.expense_categories (
    id integer NOT NULL,
    name character varying(50),
    description text,
    color character varying(7) DEFAULT '#6c757d'::character varying,
    monthly_budget numeric(10,2) DEFAULT NULL::numeric,
    icon character varying(500) DEFAULT NULL::character varying
);


ALTER TABLE public.expense_categories OWNER TO grubb;

--
-- TOC entry 227 (class 1259 OID 16438)
-- Name: expense_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.expense_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.expense_categories_id_seq OWNER TO grubb;

--
-- TOC entry 3604 (class 0 OID 0)
-- Dependencies: 227
-- Name: expense_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.expense_categories_id_seq OWNED BY public.expense_categories.id;


--
-- TOC entry 242 (class 1259 OID 16561)
-- Name: expense_changes; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.expense_changes (
    id integer NOT NULL,
    recurring_schedule_id integer,
    effective_date date NOT NULL,
    end_date date,
    new_amount numeric(10,2) NOT NULL
);


ALTER TABLE public.expense_changes OWNER TO grubb;

--
-- TOC entry 241 (class 1259 OID 16560)
-- Name: expense_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.expense_changes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.expense_changes_id_seq OWNER TO grubb;

--
-- TOC entry 3605 (class 0 OID 0)
-- Dependencies: 241
-- Name: expense_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.expense_changes_id_seq OWNED BY public.expense_changes.id;


--
-- TOC entry 246 (class 1259 OID 16590)
-- Name: expense_payments; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.expense_payments (
    id integer NOT NULL,
    expense_id integer NOT NULL,
    account_id integer NOT NULL,
    payment_date date NOT NULL,
    amount numeric(10,2) NOT NULL
);


ALTER TABLE public.expense_payments OWNER TO grubb;

--
-- TOC entry 245 (class 1259 OID 16589)
-- Name: expense_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.expense_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.expense_payments_id_seq OWNER TO grubb;

--
-- TOC entry 3606 (class 0 OID 0)
-- Dependencies: 245
-- Name: expense_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.expense_payments_id_seq OWNED BY public.expense_payments.id;


--
-- TOC entry 240 (class 1259 OID 16537)
-- Name: expenses; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.expenses (
    id integer NOT NULL,
    user_id integer NOT NULL,
    scheduled_date date NOT NULL,
    category_id integer,
    amount numeric(10,2) NOT NULL,
    description text,
    paid boolean NOT NULL,
    recurring_schedule_id integer,
    notes text,
    paycheck_id integer
);


ALTER TABLE public.expenses OWNER TO grubb;

--
-- TOC entry 239 (class 1259 OID 16536)
-- Name: expenses_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.expenses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.expenses_id_seq OWNER TO grubb;

--
-- TOC entry 3607 (class 0 OID 0)
-- Dependencies: 239
-- Name: expenses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.expenses_id_seq OWNED BY public.expenses.id;


--
-- TOC entry 224 (class 1259 OID 16419)
-- Name: frequencies; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.frequencies (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    description text
);


ALTER TABLE public.frequencies OWNER TO grubb;

--
-- TOC entry 223 (class 1259 OID 16418)
-- Name: frequencies_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.frequencies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.frequencies_id_seq OWNER TO grubb;

--
-- TOC entry 3608 (class 0 OID 0)
-- Dependencies: 223
-- Name: frequencies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.frequencies_id_seq OWNED BY public.frequencies.id;


--
-- TOC entry 226 (class 1259 OID 16430)
-- Name: income_categories; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.income_categories (
    id integer NOT NULL,
    name character varying(50),
    description text,
    color character varying(7) DEFAULT NULL::character varying,
    icon character varying(500) DEFAULT NULL::character varying
);


ALTER TABLE public.income_categories OWNER TO grubb;

--
-- TOC entry 225 (class 1259 OID 16429)
-- Name: income_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.income_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.income_categories_id_seq OWNER TO grubb;

--
-- TOC entry 3609 (class 0 OID 0)
-- Dependencies: 225
-- Name: income_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.income_categories_id_seq OWNED BY public.income_categories.id;


--
-- TOC entry 244 (class 1259 OID 16573)
-- Name: income_payments; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.income_payments (
    id integer NOT NULL,
    paycheck_id integer NOT NULL,
    account_id integer NOT NULL,
    payment_date date NOT NULL,
    amount numeric(10,2) NOT NULL,
    is_percentage boolean DEFAULT false,
    percentage numeric(5,2)
);


ALTER TABLE public.income_payments OWNER TO grubb;

--
-- TOC entry 243 (class 1259 OID 16572)
-- Name: income_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.income_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.income_payments_id_seq OWNER TO grubb;

--
-- TOC entry 3610 (class 0 OID 0)
-- Dependencies: 243
-- Name: income_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.income_payments_id_seq OWNED BY public.income_payments.id;


--
-- TOC entry 238 (class 1259 OID 16515)
-- Name: paychecks; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.paychecks (
    id integer NOT NULL,
    user_id integer NOT NULL,
    scheduled_date date NOT NULL,
    gross_salary numeric(10,2) NOT NULL,
    taxes numeric(10,2),
    deductions numeric(10,2),
    net_salary numeric(10,2),
    is_projected boolean NOT NULL,
    category_id integer,
    recurring_schedule_id integer,
    paid boolean NOT NULL
);


ALTER TABLE public.paychecks OWNER TO grubb;

--
-- TOC entry 237 (class 1259 OID 16514)
-- Name: paychecks_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.paychecks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.paychecks_id_seq OWNER TO grubb;

--
-- TOC entry 3611 (class 0 OID 0)
-- Dependencies: 237
-- Name: paychecks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.paychecks_id_seq OWNED BY public.paychecks.id;


--
-- TOC entry 234 (class 1259 OID 16481)
-- Name: recurring_schedules; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.recurring_schedules (
    id integer NOT NULL,
    user_id integer NOT NULL,
    type_id integer,
    description character varying(255),
    frequency_id integer,
    "interval" integer,
    start_date date NOT NULL,
    end_date date,
    amount numeric(10,2) NOT NULL,
    category_type character varying(20),
    category_id integer,
    default_account_id integer
);


ALTER TABLE public.recurring_schedules OWNER TO grubb;

--
-- TOC entry 3612 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN recurring_schedules.category_type; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.recurring_schedules.category_type IS 'Type of category - either "income" or "expense"';


--
-- TOC entry 3613 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN recurring_schedules.category_id; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.recurring_schedules.category_id IS 'ID of the category (references income_categories or expense_categories depending on category_type)';


--
-- TOC entry 3614 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN recurring_schedules.default_account_id; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.recurring_schedules.default_account_id IS 'Default account to use for payments/deposits';


--
-- TOC entry 233 (class 1259 OID 16480)
-- Name: recurring_schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.recurring_schedules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.recurring_schedules_id_seq OWNER TO grubb;

--
-- TOC entry 3615 (class 0 OID 0)
-- Dependencies: 233
-- Name: recurring_schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.recurring_schedules_id_seq OWNED BY public.recurring_schedules.id;


--
-- TOC entry 218 (class 1259 OID 16390)
-- Name: roles; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.roles (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    description text
);


ALTER TABLE public.roles OWNER TO grubb;

--
-- TOC entry 217 (class 1259 OID 16389)
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.roles_id_seq OWNER TO grubb;

--
-- TOC entry 3616 (class 0 OID 0)
-- Dependencies: 217
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- TOC entry 236 (class 1259 OID 16503)
-- Name: salary_changes; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.salary_changes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    effective_date date NOT NULL,
    end_date date,
    gross_annual_salary numeric(10,2) NOT NULL,
    federal_tax_rate numeric(5,2) DEFAULT 22.0,
    state_tax_rate numeric(5,2) DEFAULT 5.0,
    retirement_contribution_rate numeric(5,2) DEFAULT 6.0,
    health_insurance_amount numeric(10,2) DEFAULT 249.0,
    other_deductions_amount numeric(10,2) DEFAULT 100.0,
    notes text
);


ALTER TABLE public.salary_changes OWNER TO grubb;

--
-- TOC entry 3617 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.federal_tax_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.federal_tax_rate IS 'Federal tax rate as a percentage';


--
-- TOC entry 3618 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.state_tax_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.state_tax_rate IS 'State tax rate as a percentage';


--
-- TOC entry 3619 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.retirement_contribution_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.retirement_contribution_rate IS 'Retirement contribution rate as a percentage';


--
-- TOC entry 3620 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.health_insurance_amount; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.health_insurance_amount IS 'Health insurance amount per paycheck';


--
-- TOC entry 3621 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.other_deductions_amount; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.other_deductions_amount IS 'Other deductions amount per paycheck';


--
-- TOC entry 3622 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.notes; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.notes IS 'Additional notes about the salary';


--
-- TOC entry 235 (class 1259 OID 16502)
-- Name: salary_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.salary_changes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.salary_changes_id_seq OWNER TO grubb;

--
-- TOC entry 3623 (class 0 OID 0)
-- Dependencies: 235
-- Name: salary_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.salary_changes_id_seq OWNED BY public.salary_changes.id;


--
-- TOC entry 252 (class 1259 OID 16639)
-- Name: salary_deposit_allocations; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.salary_deposit_allocations (
    id integer NOT NULL,
    salary_id integer NOT NULL,
    account_id integer NOT NULL,
    is_percentage boolean,
    percentage numeric(5,2),
    amount numeric(10,2)
);


ALTER TABLE public.salary_deposit_allocations OWNER TO grubb;

--
-- TOC entry 251 (class 1259 OID 16638)
-- Name: salary_deposit_allocations_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.salary_deposit_allocations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.salary_deposit_allocations_id_seq OWNER TO grubb;

--
-- TOC entry 3624 (class 0 OID 0)
-- Dependencies: 251
-- Name: salary_deposit_allocations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.salary_deposit_allocations_id_seq OWNED BY public.salary_deposit_allocations.id;


--
-- TOC entry 222 (class 1259 OID 16408)
-- Name: schedule_types; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.schedule_types (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    description text
);


ALTER TABLE public.schedule_types OWNER TO grubb;

--
-- TOC entry 221 (class 1259 OID 16407)
-- Name: schedule_types_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.schedule_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.schedule_types_id_seq OWNER TO grubb;

--
-- TOC entry 3625 (class 0 OID 0)
-- Dependencies: 221
-- Name: schedule_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.schedule_types_id_seq OWNED BY public.schedule_types.id;


--
-- TOC entry 248 (class 1259 OID 16608)
-- Name: transactions; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.transactions (
    id integer NOT NULL,
    account_id integer NOT NULL,
    transaction_date date NOT NULL,
    amount numeric(10,2) NOT NULL,
    description character varying(255),
    transaction_type character varying(50),
    related_transaction_id integer
);


ALTER TABLE public.transactions OWNER TO grubb;

--
-- TOC entry 247 (class 1259 OID 16607)
-- Name: transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.transactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.transactions_id_seq OWNER TO grubb;

--
-- TOC entry 3626 (class 0 OID 0)
-- Dependencies: 247
-- Name: transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.transactions_id_seq OWNED BY public.transactions.id;


--
-- TOC entry 254 (class 1259 OID 16683)
-- Name: user_preferences; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.user_preferences (
    id integer NOT NULL,
    user_id integer NOT NULL,
    preference_key character varying(100) NOT NULL,
    preference_value character varying(255)
);


ALTER TABLE public.user_preferences OWNER TO grubb;

--
-- TOC entry 253 (class 1259 OID 16682)
-- Name: user_preferences_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.user_preferences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_preferences_id_seq OWNER TO grubb;

--
-- TOC entry 3627 (class 0 OID 0)
-- Dependencies: 253
-- Name: user_preferences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.user_preferences_id_seq OWNED BY public.user_preferences.id;


--
-- TOC entry 230 (class 1259 OID 16448)
-- Name: users; Type: TABLE; Schema: public; Owner: grubb
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash text NOT NULL,
    email character varying(100),
    role_id integer,
    first_name character varying(50),
    last_name character varying(50)
);


ALTER TABLE public.users OWNER TO grubb;

--
-- TOC entry 229 (class 1259 OID 16447)
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: grubb
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO grubb;

--
-- TOC entry 3628 (class 0 OID 0)
-- Dependencies: 229
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- TOC entry 3327 (class 2604 OID 16623)
-- Name: account_interest id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest ALTER COLUMN id SET DEFAULT nextval('public.account_interest_id_seq'::regclass);


--
-- TOC entry 3301 (class 2604 OID 16404)
-- Name: account_types id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_types ALTER COLUMN id SET DEFAULT nextval('public.account_types_id_seq'::regclass);


--
-- TOC entry 3312 (class 2604 OID 16467)
-- Name: accounts id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);


--
-- TOC entry 3307 (class 2604 OID 16442)
-- Name: expense_categories id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_categories ALTER COLUMN id SET DEFAULT nextval('public.expense_categories_id_seq'::regclass);


--
-- TOC entry 3322 (class 2604 OID 16564)
-- Name: expense_changes id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes ALTER COLUMN id SET DEFAULT nextval('public.expense_changes_id_seq'::regclass);


--
-- TOC entry 3325 (class 2604 OID 16593)
-- Name: expense_payments id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments ALTER COLUMN id SET DEFAULT nextval('public.expense_payments_id_seq'::regclass);


--
-- TOC entry 3321 (class 2604 OID 16540)
-- Name: expenses id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses ALTER COLUMN id SET DEFAULT nextval('public.expenses_id_seq'::regclass);


--
-- TOC entry 3303 (class 2604 OID 16422)
-- Name: frequencies id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies ALTER COLUMN id SET DEFAULT nextval('public.frequencies_id_seq'::regclass);


--
-- TOC entry 3304 (class 2604 OID 16433)
-- Name: income_categories id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_categories ALTER COLUMN id SET DEFAULT nextval('public.income_categories_id_seq'::regclass);


--
-- TOC entry 3323 (class 2604 OID 16576)
-- Name: income_payments id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments ALTER COLUMN id SET DEFAULT nextval('public.income_payments_id_seq'::regclass);


--
-- TOC entry 3320 (class 2604 OID 16518)
-- Name: paychecks id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks ALTER COLUMN id SET DEFAULT nextval('public.paychecks_id_seq'::regclass);


--
-- TOC entry 3313 (class 2604 OID 16484)
-- Name: recurring_schedules id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules ALTER COLUMN id SET DEFAULT nextval('public.recurring_schedules_id_seq'::regclass);


--
-- TOC entry 3300 (class 2604 OID 16393)
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- TOC entry 3314 (class 2604 OID 16506)
-- Name: salary_changes id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes ALTER COLUMN id SET DEFAULT nextval('public.salary_changes_id_seq'::regclass);


--
-- TOC entry 3331 (class 2604 OID 16642)
-- Name: salary_deposit_allocations id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations ALTER COLUMN id SET DEFAULT nextval('public.salary_deposit_allocations_id_seq'::regclass);


--
-- TOC entry 3302 (class 2604 OID 16411)
-- Name: schedule_types id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types ALTER COLUMN id SET DEFAULT nextval('public.schedule_types_id_seq'::regclass);


--
-- TOC entry 3326 (class 2604 OID 16611)
-- Name: transactions id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions ALTER COLUMN id SET DEFAULT nextval('public.transactions_id_seq'::regclass);


--
-- TOC entry 3332 (class 2604 OID 16686)
-- Name: user_preferences id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.user_preferences ALTER COLUMN id SET DEFAULT nextval('public.user_preferences_id_seq'::regclass);


--
-- TOC entry 3311 (class 2604 OID 16451)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 3590 (class 0 OID 16620)
-- Dependencies: 250
-- Data for Name: account_interest; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.account_interest (id, account_id, rate, compound_frequency, accrual_day, interest_type, enabled, last_accrual_date) FROM stdin;
1	3	7.00	daily	\N	compound	f	\N
2	2	10.00	daily	\N	compound	f	2025-03-01
3	4	20.00	daily	15	compound	f	2025-03-01
\.


--
-- TOC entry 3560 (class 0 OID 16401)
-- Dependencies: 220
-- Data for Name: account_types; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.account_types (id, type_name, is_debt) FROM stdin;
1	Checking	f
2	Savings	f
3	Cash	f
4	Money Market	f
5	Certificate of Deposit	f
6	Investment	f
7	Retirement	f
8	HSA	f
9	Other Asset	f
10	Credit Card	t
11	Mortgage	t
12	Auto Loan	t
13	Student Loan	t
14	Personal Loan	t
15	Line of Credit	t
16	Medical Debt	t
17	Other Debt	t
\.


--
-- TOC entry 3572 (class 0 OID 16464)
-- Dependencies: 232
-- Data for Name: accounts; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.accounts (id, user_id, account_name, type_id, balance) FROM stdin;
3	1	Home Equity	9	340000.00
5	1	Fidelity Money Market	4	500.00
4	1	CapitalOne Credit Card	10	1551.78
1	1	SECU Checking	1	-232.63
2	1	Mortgage	11	181521.26
6	1	SECU Savings	2	25.71
7	1	Bank of America Van Loan	12	22726.35
\.


--
-- TOC entry 3568 (class 0 OID 16439)
-- Dependencies: 228
-- Data for Name: expense_categories; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expense_categories (id, name, description, color, monthly_budget, icon) FROM stdin;
1	Auto	Car payment, Gas, Insurance, Maintenance, Tax	#6c757d	\N	M22 13.5v2c0 0.5-0.5 1-1 1h-1c-0.5 0-1-0.5-1-1v-1H5v1c0 0.5-0.5 1-1 1H3c-0.5 0-1-0.5-1-1v-2l2-8h16L22 13.5z M7 14h10 M3.5 13.5h1 M19.5 13.5h1 M6 10h12 M19 6H5L3 14.1V16a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h12v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1.9L19 6z
2	Debt	Credit card	#e01b24	\N	M2 8a5 5 0 1 0 10 0A5 5 0 0 0 2 8z M10 9H4 M7 6v6 M12 8a5 5 0 1 0 10 0A5 5 0 0 0 12 8z M22 11H12
3	Entertainment	Apple, Audible, Disney+, Kindle	#813d9c	\N	M7 4v16l13-8L7 4z
4	Family	Birthday's, Christmas, Clothes, School, Spending money	#ffbe6f	\N	M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M23 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75
5	Food	Groceries, Date Night	#b5835a	\N	M18 8h1a4 4 0 0 1 0 8h-1 M5 8h11.5 M5 12h11.5 M5 16h11.5 M2 8h1a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H2
6	Home	Mortgage, Utilities, Maintenance	#33d17a	\N	M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9z M9 22V12h6v10
7	Technology	Phones, Computer, Network, Email, Password manager	#62a0ea	\N	M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M22 8h-4 M6 18h12 M6 14h12 M6 10h12
8	Savings	Retirement, Emergency Fund	#26a269	\N	M12 2a10 10 0 1 0 10 10H12V2z M18 9h-6V3
\.


--
-- TOC entry 3582 (class 0 OID 16561)
-- Dependencies: 242
-- Data for Name: expense_changes; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expense_changes (id, recurring_schedule_id, effective_date, end_date, new_amount) FROM stdin;
\.


--
-- TOC entry 3586 (class 0 OID 16590)
-- Dependencies: 246
-- Data for Name: expense_payments; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expense_payments (id, expense_id, account_id, payment_date, amount) FROM stdin;
5	2905	1	2025-03-13	100.00
6	2945	1	2025-03-13	1551.78
\.


--
-- TOC entry 3580 (class 0 OID 16537)
-- Dependencies: 240
-- Data for Name: expenses; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expenses (id, user_id, scheduled_date, category_id, amount, description, paid, recurring_schedule_id, notes, paycheck_id) FROM stdin;
93	1	2026-04-23	1	80.00	Gas	f	21	\N	\N
94	1	2026-05-07	1	80.00	Gas	f	21	\N	\N
95	1	2026-05-21	1	80.00	Gas	f	21	\N	\N
96	1	2026-06-04	1	80.00	Gas	f	21	\N	\N
97	1	2026-06-18	1	80.00	Gas	f	21	\N	\N
98	1	2026-07-02	1	80.00	Gas	f	21	\N	\N
99	1	2026-07-16	1	80.00	Gas	f	21	\N	\N
2058	1	2026-03-13	1	100.00	Mower Maintenance	f	81	\N	\N
2059	1	2027-03-13	1	100.00	Mower Maintenance	f	81	\N	\N
2906	1	2026-03-24	4	100.00	Josh's Birthday	f	122	\N	\N
2907	1	2027-03-24	4	100.00	Josh's Birthday	f	122	\N	\N
2064	1	2026-10-11	1	30.00	RAV4 State Inspection	f	83	\N	\N
2065	1	2027-10-11	1	30.00	RAV4 State Inspection	f	83	\N	\N
100	1	2026-07-30	1	80.00	Gas	f	21	\N	\N
101	1	2026-08-13	1	80.00	Gas	f	21	\N	\N
102	1	2026-08-27	1	80.00	Gas	f	21	\N	\N
2909	1	2026-06-08	4	100.00	Father's Day	f	123	\N	\N
2910	1	2027-06-08	4	100.00	Father's Day	f	123	\N	\N
2908	1	2025-06-08	4	100.00	Father's Day	f	123	\N	7110
2905	1	2025-03-13	4	100.00	Josh's Birthday	t	122		7112
37	1	2025-03-13	1	80.00	Gas	f	21	\N	7104
38	1	2025-03-27	1	80.00	Gas	f	21	\N	7105
2790	1	2025-08-14	8	500.00	Emergency Fund	f	108	\N	\N
51	1	2025-03-13	5	400.00	Groceries	f	23	\N	7104
52	1	2025-03-27	5	400.00	Groceries	f	23	\N	7105
103	1	2026-09-10	1	80.00	Gas	f	21	\N	\N
57	1	2025-03-15	3	18.14	Apple Music	f	24	\N	7104
2777	1	2025-03-24	7	240.00	Kobo Libra Colour	f	\N		7112
104	1	2026-09-24	1	80.00	Gas	f	21	\N	\N
105	1	2026-10-08	1	80.00	Gas	f	21	\N	\N
59	1	2025-05-15	3	18.14	Apple Music	f	24	\N	7108
601	1	2025-05-17	7	9.99	iCloud 2TB	f	36	\N	7108
602	1	2025-06-17	7	9.99	iCloud 2TB	f	36	\N	7110
2784	1	2025-05-22	8	500.00	Emergency Fund	f	108	\N	7109
2785	1	2025-06-05	8	500.00	Emergency Fund	f	108	\N	7110
106	1	2026-10-22	1	80.00	Gas	f	21	\N	\N
107	1	2026-11-05	1	80.00	Gas	f	21	\N	\N
108	1	2026-11-19	1	80.00	Gas	f	21	\N	\N
36	1	2025-02-27	1	80.00	Gas	t	21		\N
2791	1	2025-08-28	8	500.00	Emergency Fund	f	108	\N	\N
2792	1	2025-09-11	8	500.00	Emergency Fund	f	108	\N	\N
2793	1	2025-09-25	8	500.00	Emergency Fund	f	108	\N	\N
2794	1	2025-10-09	8	500.00	Emergency Fund	f	108	\N	\N
65	1	2025-11-15	3	18.14	Apple Music	f	24	\N	\N
66	1	2025-12-15	3	18.14	Apple Music	f	24	\N	\N
2795	1	2025-10-23	8	500.00	Emergency Fund	f	108	\N	\N
2796	1	2025-11-06	8	500.00	Emergency Fund	f	108	\N	\N
2786	1	2025-06-19	8	500.00	Emergency Fund	f	108	\N	7111
2797	1	2025-11-20	8	500.00	Emergency Fund	f	108	\N	\N
2798	1	2025-12-04	8	500.00	Emergency Fund	f	108	\N	\N
61	1	2025-07-15	3	18.14	Apple Music	f	24	\N	7111
2787	1	2025-07-03	8	500.00	Emergency Fund	f	108	\N	7111
2788	1	2025-07-17	8	500.00	Emergency Fund	f	108	\N	7111
2789	1	2025-07-31	8	500.00	Emergency Fund	f	108	\N	7111
80	1	2025-10-23	1	80.00	Gas	f	21	\N	\N
81	1	2025-11-06	1	80.00	Gas	f	21	\N	\N
82	1	2025-11-20	1	80.00	Gas	f	21	\N	\N
83	1	2025-12-04	1	80.00	Gas	f	21	\N	\N
84	1	2025-12-18	1	80.00	Gas	f	21	\N	\N
85	1	2026-01-01	1	80.00	Gas	f	21	\N	\N
86	1	2026-01-15	1	80.00	Gas	f	21	\N	\N
87	1	2026-01-29	1	80.00	Gas	f	21	\N	\N
88	1	2026-02-12	1	80.00	Gas	f	21	\N	\N
89	1	2026-02-26	1	80.00	Gas	f	21	\N	\N
90	1	2026-03-12	1	80.00	Gas	f	21	\N	\N
91	1	2026-03-26	1	80.00	Gas	f	21	\N	\N
92	1	2026-04-09	1	80.00	Gas	f	21	\N	\N
109	1	2026-12-03	1	80.00	Gas	f	21	\N	\N
110	1	2026-12-17	1	80.00	Gas	f	21	\N	\N
111	1	2026-12-31	1	80.00	Gas	f	21	\N	\N
112	1	2027-01-14	1	80.00	Gas	f	21	\N	\N
113	1	2027-01-28	1	80.00	Gas	f	21	\N	\N
114	1	2027-02-11	1	80.00	Gas	f	21	\N	\N
115	1	2027-02-25	1	80.00	Gas	f	21	\N	\N
116	1	2027-03-11	1	80.00	Gas	f	21	\N	\N
117	1	2027-03-25	1	80.00	Gas	f	21	\N	\N
118	1	2027-04-08	1	80.00	Gas	f	21	\N	\N
119	1	2027-04-22	1	80.00	Gas	f	21	\N	\N
131	1	2025-10-23	5	400.00	Groceries	f	23	\N	\N
132	1	2025-11-06	5	400.00	Groceries	f	23	\N	\N
133	1	2025-11-20	5	400.00	Groceries	f	23	\N	\N
134	1	2025-12-04	5	400.00	Groceries	f	23	\N	\N
135	1	2025-12-18	5	400.00	Groceries	f	23	\N	\N
136	1	2026-01-01	5	400.00	Groceries	f	23	\N	\N
137	1	2026-01-15	5	400.00	Groceries	f	23	\N	\N
138	1	2026-01-29	5	400.00	Groceries	f	23	\N	\N
139	1	2026-02-12	5	400.00	Groceries	f	23	\N	\N
140	1	2026-02-26	5	400.00	Groceries	f	23	\N	\N
141	1	2026-03-12	5	400.00	Groceries	f	23	\N	\N
142	1	2026-03-26	5	400.00	Groceries	f	23	\N	\N
143	1	2026-04-09	5	400.00	Groceries	f	23	\N	\N
144	1	2026-04-23	5	400.00	Groceries	f	23	\N	\N
145	1	2026-05-07	5	400.00	Groceries	f	23	\N	\N
146	1	2026-05-21	5	400.00	Groceries	f	23	\N	\N
147	1	2026-06-04	5	400.00	Groceries	f	23	\N	\N
148	1	2026-06-18	5	400.00	Groceries	f	23	\N	\N
149	1	2026-07-02	5	400.00	Groceries	f	23	\N	\N
150	1	2026-07-16	5	400.00	Groceries	f	23	\N	\N
151	1	2026-07-30	5	400.00	Groceries	f	23	\N	\N
152	1	2026-08-13	5	400.00	Groceries	f	23	\N	\N
153	1	2026-08-27	5	400.00	Groceries	f	23	\N	\N
154	1	2026-09-10	5	400.00	Groceries	f	23	\N	\N
155	1	2026-09-24	5	400.00	Groceries	f	23	\N	\N
156	1	2026-10-08	5	400.00	Groceries	f	23	\N	\N
157	1	2026-10-22	5	400.00	Groceries	f	23	\N	\N
158	1	2026-11-05	5	400.00	Groceries	f	23	\N	\N
159	1	2026-11-19	5	400.00	Groceries	f	23	\N	\N
160	1	2026-12-03	5	400.00	Groceries	f	23	\N	\N
161	1	2026-12-17	5	400.00	Groceries	f	23	\N	\N
162	1	2026-12-31	5	400.00	Groceries	f	23	\N	\N
163	1	2027-01-14	5	400.00	Groceries	f	23	\N	\N
164	1	2027-01-28	5	400.00	Groceries	f	23	\N	\N
165	1	2027-02-11	5	400.00	Groceries	f	23	\N	\N
166	1	2027-02-25	5	400.00	Groceries	f	23	\N	\N
167	1	2027-03-11	5	400.00	Groceries	f	23	\N	\N
168	1	2027-03-25	5	400.00	Groceries	f	23	\N	\N
169	1	2026-01-15	3	18.14	Apple Music	f	24	\N	\N
170	1	2026-02-15	3	18.14	Apple Music	f	24	\N	\N
171	1	2026-03-15	3	18.14	Apple Music	f	24	\N	\N
172	1	2026-04-15	3	18.14	Apple Music	f	24	\N	\N
173	1	2026-05-15	3	18.14	Apple Music	f	24	\N	\N
174	1	2026-06-15	3	18.14	Apple Music	f	24	\N	\N
175	1	2026-07-15	3	18.14	Apple Music	f	24	\N	\N
176	1	2026-08-15	3	18.14	Apple Music	f	24	\N	\N
177	1	2026-09-15	3	18.14	Apple Music	f	24	\N	\N
178	1	2026-10-15	3	18.14	Apple Music	f	24	\N	\N
179	1	2026-11-15	3	18.14	Apple Music	f	24	\N	\N
180	1	2026-12-15	3	18.14	Apple Music	f	24	\N	\N
181	1	2027-01-15	3	18.14	Apple Music	f	24	\N	\N
182	1	2027-02-15	3	18.14	Apple Music	f	24	\N	\N
183	1	2027-03-15	3	18.14	Apple Music	f	24	\N	\N
2799	1	2025-12-18	8	500.00	Emergency Fund	f	108	\N	\N
2800	1	2026-01-01	8	500.00	Emergency Fund	f	108	\N	\N
2061	1	2026-10-11	1	122.98	RAV4 Property Tax	f	82	\N	\N
2062	1	2027-10-11	1	122.98	RAV4 Property Tax	f	82	\N	\N
2066	1	2026-01-11	1	30.00	Van State Inspection	f	84	\N	\N
2801	1	2026-01-15	8	500.00	Emergency Fund	f	108	\N	\N
2802	1	2026-01-29	8	500.00	Emergency Fund	f	108	\N	\N
2803	1	2026-02-12	8	500.00	Emergency Fund	f	108	\N	\N
2804	1	2026-02-26	8	500.00	Emergency Fund	f	108	\N	\N
192	1	2025-11-20	1	531.94	Van Payment	f	25	\N	\N
193	1	2025-12-20	1	531.94	Van Payment	f	25	\N	\N
194	1	2026-01-20	1	531.94	Van Payment	f	25	\N	\N
195	1	2026-02-20	1	531.94	Van Payment	f	25	\N	\N
196	1	2026-03-20	1	531.94	Van Payment	f	25	\N	\N
197	1	2026-04-20	1	531.94	Van Payment	f	25	\N	\N
198	1	2026-05-20	1	531.94	Van Payment	f	25	\N	\N
199	1	2026-06-20	1	531.94	Van Payment	f	25	\N	\N
200	1	2026-07-20	1	531.94	Van Payment	f	25	\N	\N
201	1	2026-08-20	1	531.94	Van Payment	f	25	\N	\N
202	1	2026-09-20	1	531.94	Van Payment	f	25	\N	\N
203	1	2026-10-20	1	531.94	Van Payment	f	25	\N	\N
204	1	2026-11-20	1	531.94	Van Payment	f	25	\N	\N
205	1	2026-12-20	1	531.94	Van Payment	f	25	\N	\N
206	1	2027-01-20	1	531.94	Van Payment	f	25	\N	\N
207	1	2027-02-20	1	531.94	Van Payment	f	25	\N	\N
208	1	2027-03-20	1	531.94	Van Payment	f	25	\N	\N
209	1	2027-04-20	1	531.94	Van Payment	f	25	\N	\N
210	1	2027-05-20	1	531.94	Van Payment	f	25	\N	\N
211	1	2027-06-20	1	531.94	Van Payment	f	25	\N	\N
212	1	2027-07-20	1	531.94	Van Payment	f	25	\N	\N
213	1	2027-08-20	1	531.94	Van Payment	f	25	\N	\N
214	1	2027-09-20	1	531.94	Van Payment	f	25	\N	\N
215	1	2027-10-20	1	531.94	Van Payment	f	25	\N	\N
216	1	2027-11-20	1	531.94	Van Payment	f	25	\N	\N
217	1	2027-12-20	1	531.94	Van Payment	f	25	\N	\N
218	1	2028-01-20	1	531.94	Van Payment	f	25	\N	\N
219	1	2028-02-20	1	531.94	Van Payment	f	25	\N	\N
220	1	2028-03-20	1	531.94	Van Payment	f	25	\N	\N
221	1	2028-04-20	1	531.94	Van Payment	f	25	\N	\N
222	1	2028-05-20	1	531.94	Van Payment	f	25	\N	\N
223	1	2028-06-20	1	531.94	Van Payment	f	25	\N	\N
224	1	2028-07-20	1	531.94	Van Payment	f	25	\N	\N
225	1	2028-08-20	1	531.94	Van Payment	f	25	\N	\N
226	1	2028-09-20	1	531.94	Van Payment	f	25	\N	\N
227	1	2028-10-20	1	531.94	Van Payment	f	25	\N	\N
228	1	2028-11-20	1	531.94	Van Payment	f	25	\N	\N
229	1	2028-12-20	1	531.94	Van Payment	f	25	\N	\N
230	1	2029-01-20	1	531.94	Van Payment	f	25	\N	\N
231	1	2029-02-20	1	531.94	Van Payment	f	25	\N	\N
2067	1	2027-01-11	1	30.00	Van State Inspection	f	84	\N	\N
2912	1	2026-05-04	4	100.00	Mother's Day	f	124	\N	\N
2913	1	2027-05-04	4	100.00	Mother's Day	f	124	\N	\N
2805	1	2026-03-12	8	500.00	Emergency Fund	f	108	\N	\N
2926	1	2025-07-01	4	600.00	School Curriculum	f	129	\N	7111
239	1	2025-11-01	1	183.49	Geico Car Insurance	f	26	\N	\N
240	1	2025-12-01	1	183.49	Geico Car Insurance	f	26	\N	\N
241	1	2026-01-01	1	183.49	Geico Car Insurance	f	26	\N	\N
242	1	2026-02-01	1	183.49	Geico Car Insurance	f	26	\N	\N
243	1	2026-03-01	1	183.49	Geico Car Insurance	f	26	\N	\N
244	1	2026-04-01	1	183.49	Geico Car Insurance	f	26	\N	\N
245	1	2026-05-01	1	183.49	Geico Car Insurance	f	26	\N	\N
246	1	2026-06-01	1	183.49	Geico Car Insurance	f	26	\N	\N
247	1	2026-07-01	1	183.49	Geico Car Insurance	f	26	\N	\N
248	1	2026-08-01	1	183.49	Geico Car Insurance	f	26	\N	\N
249	1	2026-09-01	1	183.49	Geico Car Insurance	f	26	\N	\N
250	1	2026-10-01	1	183.49	Geico Car Insurance	f	26	\N	\N
251	1	2026-11-01	1	183.49	Geico Car Insurance	f	26	\N	\N
252	1	2026-12-01	1	183.49	Geico Car Insurance	f	26	\N	\N
253	1	2027-01-01	1	183.49	Geico Car Insurance	f	26	\N	\N
254	1	2027-02-01	1	183.49	Geico Car Insurance	f	26	\N	\N
255	1	2027-03-01	1	183.49	Geico Car Insurance	f	26	\N	\N
256	1	2027-04-01	1	183.49	Geico Car Insurance	f	26	\N	\N
2069	1	2026-01-11	1	208.17	Van Property Tax	f	85	\N	\N
2070	1	2027-01-11	1	208.17	Van Property Tax	f	85	\N	\N
2915	1	2026-04-10	5	200.00	Strawberry Picking	f	125	\N	\N
2916	1	2027-04-10	5	200.00	Strawberry Picking	f	125	\N	\N
2929	1	2025-10-18	4	100.00	Wedding Anniversary	f	130	\N	\N
2930	1	2026-10-18	4	100.00	Wedding Anniversary	f	130	\N	\N
2931	1	2027-10-18	4	100.00	Wedding Anniversary	f	130	\N	\N
2942	1	2026-02-22	4	100.00	Knox's Birthday	f	135	\N	\N
2943	1	2027-02-22	4	100.00	Knox's Birthday	f	135	\N	\N
2914	1	2025-04-10	5	200.00	Strawberry Picking	f	125	\N	7106
2806	1	2026-03-26	8	500.00	Emergency Fund	f	108	\N	\N
2807	1	2026-04-09	8	500.00	Emergency Fund	f	108	\N	\N
313	1	2026-04-11	1	175.00	Oil & Air Filters	f	28	\N	\N
314	1	2026-10-11	1	175.00	Oil & Air Filters	f	28	\N	\N
315	1	2027-04-11	1	175.00	Oil & Air Filters	f	28	\N	\N
2071	1	2026-01-08	4	100.00	Eliana's Birthday	f	86	\N	\N
2072	1	2027-01-08	4	100.00	Eliana's Birthday	f	86	\N	\N
2917	1	2025-08-21	4	100.00	New Baby Birthday	f	126	\N	\N
2918	1	2026-08-21	4	100.00	New Baby Birthday	f	126	\N	\N
2919	1	2027-08-21	4	100.00	New Baby Birthday	f	126	\N	\N
2932	1	2025-11-13	7	1300.00	Josh's New Phone	f	131	\N	\N
2933	1	2027-11-13	7	1300.00	Josh's New Phone	f	131	\N	\N
2944	1	2026-02-14	4	100.00	Valentine's Day	f	136	\N	\N
2808	1	2026-04-23	8	500.00	Emergency Fund	f	108	\N	\N
2809	1	2026-05-07	8	500.00	Emergency Fund	f	108	\N	\N
2810	1	2026-05-21	8	500.00	Emergency Fund	f	108	\N	\N
2811	1	2026-06-04	8	500.00	Emergency Fund	f	108	\N	\N
2812	1	2026-06-18	8	500.00	Emergency Fund	f	108	\N	\N
2813	1	2026-07-02	8	500.00	Emergency Fund	f	108	\N	\N
2814	1	2026-07-16	8	500.00	Emergency Fund	f	108	\N	\N
2815	1	2026-07-30	8	500.00	Emergency Fund	f	108	\N	\N
2816	1	2026-08-13	8	500.00	Emergency Fund	f	108	\N	\N
2817	1	2026-08-27	8	500.00	Emergency Fund	f	108	\N	\N
2818	1	2026-09-10	8	500.00	Emergency Fund	f	108	\N	\N
2819	1	2026-09-24	8	500.00	Emergency Fund	f	108	\N	\N
2820	1	2026-10-08	8	500.00	Emergency Fund	f	108	\N	\N
2821	1	2026-10-22	8	500.00	Emergency Fund	f	108	\N	\N
2822	1	2026-11-05	8	500.00	Emergency Fund	f	108	\N	\N
2823	1	2026-11-19	8	500.00	Emergency Fund	f	108	\N	\N
2824	1	2026-12-03	8	500.00	Emergency Fund	f	108	\N	\N
2825	1	2026-12-17	8	500.00	Emergency Fund	f	108	\N	\N
2826	1	2026-12-31	8	500.00	Emergency Fund	f	108	\N	\N
2827	1	2027-01-14	8	500.00	Emergency Fund	f	108	\N	\N
2828	1	2027-01-28	8	500.00	Emergency Fund	f	108	\N	\N
2829	1	2027-02-11	8	500.00	Emergency Fund	f	108	\N	\N
2830	1	2027-02-25	8	500.00	Emergency Fund	f	108	\N	\N
2831	1	2027-03-11	8	500.00	Emergency Fund	f	108	\N	\N
2832	1	2027-03-25	8	500.00	Emergency Fund	f	108	\N	\N
39	1	2025-04-10	1	80.00	Gas	f	21	\N	7106
53	1	2025-04-10	5	400.00	Groceries	f	23	\N	7106
54	1	2025-04-24	5	400.00	Groceries	f	23	\N	7107
55	1	2025-05-08	5	400.00	Groceries	f	23	\N	7108
58	1	2025-04-15	3	18.14	Apple Music	f	24	\N	7106
2778	1	2025-03-28	1	250.00	Van Hatch Control Board	f	\N		7105
2779	1	2025-03-13	8	500.00	Emergency Fund	f	108	\N	7104
2780	1	2025-03-27	8	500.00	Emergency Fund	f	108	\N	7105
2781	1	2025-04-10	8	500.00	Emergency Fund	f	108	\N	7106
2782	1	2025-04-24	8	500.00	Emergency Fund	f	108	\N	7107
2783	1	2025-05-08	8	500.00	Emergency Fund	f	108	\N	7108
2920	1	2025-10-07	4	100.00	Ariella's Birthday	f	127	\N	\N
2074	1	2026-05-01	4	100.00	Kayla's Birthday	f	87	\N	\N
2075	1	2027-05-01	4	100.00	Kayla's Birthday	f	87	\N	\N
2921	1	2026-10-07	4	100.00	Ariella's Birthday	f	127	\N	\N
2922	1	2027-10-07	4	100.00	Ariella's Birthday	f	127	\N	\N
2934	1	2025-08-13	7	288.00	Proton Family	f	132	\N	\N
2935	1	2026-08-13	7	288.00	Proton Family	f	132	\N	\N
2936	1	2027-08-13	7	288.00	Proton Family	f	132	\N	\N
553	1	2025-07-26	3	15.96	Audible	f	34	\N	7111
556	1	2025-10-26	3	15.96	Audible	f	34	\N	\N
557	1	2025-11-26	3	15.96	Audible	f	34	\N	\N
558	1	2025-12-26	3	15.96	Audible	f	34	\N	\N
559	1	2026-01-26	3	15.96	Audible	f	34	\N	\N
560	1	2026-02-26	3	15.96	Audible	f	34	\N	\N
561	1	2026-03-26	3	15.96	Audible	f	34	\N	\N
562	1	2026-04-26	3	15.96	Audible	f	34	\N	\N
563	1	2026-05-26	3	15.96	Audible	f	34	\N	\N
564	1	2026-06-26	3	15.96	Audible	f	34	\N	\N
565	1	2026-07-26	3	15.96	Audible	f	34	\N	\N
566	1	2026-08-26	3	15.96	Audible	f	34	\N	\N
567	1	2026-09-26	3	15.96	Audible	f	34	\N	\N
568	1	2026-10-26	3	15.96	Audible	f	34	\N	\N
569	1	2026-11-26	3	15.96	Audible	f	34	\N	\N
570	1	2026-12-26	3	15.96	Audible	f	34	\N	\N
571	1	2027-01-26	3	15.96	Audible	f	34	\N	\N
572	1	2027-02-26	3	15.96	Audible	f	34	\N	\N
573	1	2027-03-26	3	15.96	Audible	f	34	\N	\N
581	1	2025-10-26	3	17.15	Disney+	f	35	\N	\N
582	1	2025-11-26	3	17.15	Disney+	f	35	\N	\N
583	1	2025-12-26	3	17.15	Disney+	f	35	\N	\N
584	1	2026-01-26	3	17.15	Disney+	f	35	\N	\N
585	1	2026-02-26	3	17.15	Disney+	f	35	\N	\N
586	1	2026-03-26	3	17.15	Disney+	f	35	\N	\N
587	1	2026-04-26	3	17.15	Disney+	f	35	\N	\N
588	1	2026-05-26	3	17.15	Disney+	f	35	\N	\N
589	1	2026-06-26	3	17.15	Disney+	f	35	\N	\N
590	1	2026-07-26	3	17.15	Disney+	f	35	\N	\N
591	1	2026-08-26	3	17.15	Disney+	f	35	\N	\N
592	1	2026-09-26	3	17.15	Disney+	f	35	\N	\N
593	1	2026-10-26	3	17.15	Disney+	f	35	\N	\N
594	1	2026-11-26	3	17.15	Disney+	f	35	\N	\N
595	1	2026-12-26	3	17.15	Disney+	f	35	\N	\N
596	1	2027-01-26	3	17.15	Disney+	f	35	\N	\N
597	1	2027-02-26	3	17.15	Disney+	f	35	\N	\N
598	1	2027-03-26	3	17.15	Disney+	f	35	\N	\N
2923	1	2025-11-01	4	400.00	Christmas	f	128	\N	\N
2924	1	2026-11-01	4	400.00	Christmas	f	128	\N	\N
2925	1	2027-11-01	4	400.00	Christmas	f	128	\N	\N
2937	1	2026-03-01	5	55.00	BJ's Club Membership	f	133	\N	\N
2938	1	2027-03-01	5	55.00	BJ's Club Membership	f	133	\N	\N
2911	1	2025-05-04	4	100.00	Mother's Day	f	124	\N	7107
607	1	2025-11-17	7	9.99	iCloud 2TB	f	36	\N	\N
608	1	2025-12-17	7	9.99	iCloud 2TB	f	36	\N	\N
609	1	2026-01-17	7	9.99	iCloud 2TB	f	36	\N	\N
610	1	2026-02-17	7	9.99	iCloud 2TB	f	36	\N	\N
611	1	2026-03-17	7	9.99	iCloud 2TB	f	36	\N	\N
612	1	2026-04-17	7	9.99	iCloud 2TB	f	36	\N	\N
613	1	2026-05-17	7	9.99	iCloud 2TB	f	36	\N	\N
614	1	2026-06-17	7	9.99	iCloud 2TB	f	36	\N	\N
615	1	2026-07-17	7	9.99	iCloud 2TB	f	36	\N	\N
616	1	2026-08-17	7	9.99	iCloud 2TB	f	36	\N	\N
617	1	2026-09-17	7	9.99	iCloud 2TB	f	36	\N	\N
618	1	2026-10-17	7	9.99	iCloud 2TB	f	36	\N	\N
619	1	2026-11-17	7	9.99	iCloud 2TB	f	36	\N	\N
620	1	2026-12-17	7	9.99	iCloud 2TB	f	36	\N	\N
621	1	2027-01-17	7	9.99	iCloud 2TB	f	36	\N	\N
622	1	2027-02-17	7	9.99	iCloud 2TB	f	36	\N	\N
623	1	2027-03-17	7	9.99	iCloud 2TB	f	36	\N	\N
2950	1	2025-08-01	6	1670.20	Mortgage	f	137	\N	\N
2951	1	2025-09-01	6	1670.20	Mortgage	f	137	\N	\N
2952	1	2025-10-01	6	1670.20	Mortgage	f	137	\N	\N
2953	1	2025-11-01	6	1670.20	Mortgage	f	137	\N	\N
2954	1	2025-12-01	6	1670.20	Mortgage	f	137	\N	\N
2955	1	2026-01-01	6	1670.20	Mortgage	f	137	\N	\N
2956	1	2026-02-01	6	1670.20	Mortgage	f	137	\N	\N
2957	1	2026-03-01	6	1670.20	Mortgage	f	137	\N	\N
2958	1	2026-04-01	6	1670.20	Mortgage	f	137	\N	\N
2959	1	2026-05-01	6	1670.20	Mortgage	f	137	\N	\N
2960	1	2026-06-01	6	1670.20	Mortgage	f	137	\N	\N
2961	1	2026-07-01	6	1670.20	Mortgage	f	137	\N	\N
2962	1	2026-08-01	6	1670.20	Mortgage	f	137	\N	\N
2963	1	2026-09-01	6	1670.20	Mortgage	f	137	\N	\N
2964	1	2026-10-01	6	1670.20	Mortgage	f	137	\N	\N
2965	1	2026-11-01	6	1670.20	Mortgage	f	137	\N	\N
2966	1	2026-12-01	6	1670.20	Mortgage	f	137	\N	\N
2946	1	2025-04-01	6	1670.20	Mortgage	f	137	\N	7105
2947	1	2025-05-01	6	1670.20	Mortgage	f	137	\N	7107
2948	1	2025-06-01	6	1670.20	Mortgage	f	137	\N	7109
2949	1	2025-07-01	6	1670.20	Mortgage	f	137	\N	7111
2945	1	2025-03-13	2	1551.78	Credit Card	t	\N		7104
2927	1	2026-07-01	4	600.00	School Curriculum	f	129	\N	\N
2928	1	2027-07-01	4	600.00	School Curriculum	f	129	\N	\N
2940	1	2026-03-20	5	55.00	Sam's Club Membership	f	134	\N	\N
2941	1	2027-03-20	5	55.00	Sam's Club Membership	f	134	\N	\N
2939	1	2025-03-20	5	55.00	Sam's Club Membership	f	134	\N	7112
68	1	2025-05-08	1	80.00	Gas	f	21	\N	7108
60	1	2025-06-15	3	18.14	Apple Music	f	24	\N	7110
69	1	2025-05-22	1	80.00	Gas	f	21	\N	7109
70	1	2025-06-05	1	80.00	Gas	f	21	\N	7110
71	1	2025-06-19	1	80.00	Gas	f	21	\N	7111
120	1	2025-05-22	5	400.00	Groceries	f	23	\N	7109
121	1	2025-06-05	5	400.00	Groceries	f	23	\N	7110
122	1	2025-06-19	5	400.00	Groceries	f	23	\N	7111
186	1	2025-05-20	1	531.94	Van Payment	f	25	\N	7108
187	1	2025-06-20	1	531.94	Van Payment	f	25	\N	7111
234	1	2025-06-01	1	183.49	Geico Car Insurance	f	26	\N	7109
551	1	2025-05-26	3	15.96	Audible	f	34	\N	7109
552	1	2025-06-26	3	15.96	Audible	f	34	\N	7111
576	1	2025-05-26	3	17.15	Disney+	f	35	\N	7109
577	1	2025-06-26	3	17.15	Disney+	f	35	\N	7111
1045	1	2025-06-05	4	40.00	Josh's Spending Money	f	46	\N	7110
1046	1	2025-06-19	4	40.00	Josh's Spending Money	f	46	\N	7111
72	1	2025-07-03	1	80.00	Gas	f	21	\N	7111
123	1	2025-07-03	5	400.00	Groceries	f	23	\N	7111
235	1	2025-07-01	1	183.49	Geico Car Insurance	f	26	\N	7111
1047	1	2025-07-03	4	40.00	Josh's Spending Money	f	46	\N	7111
983	1	2026-03-01	4	400.00	Children's Clothes	f	44	\N	\N
984	1	2026-09-01	4	400.00	Children's Clothes	f	44	\N	\N
985	1	2027-03-01	4	400.00	Children's Clothes	f	44	\N	\N
986	1	2027-09-01	4	400.00	Children's Clothes	f	44	\N	\N
1043	1	2025-05-08	4	40.00	Josh's Spending Money	f	46	\N	7108
1044	1	2025-05-22	4	40.00	Josh's Spending Money	f	46	\N	7109
1048	1	2025-07-17	4	40.00	Josh's Spending Money	f	46	\N	7111
1049	1	2025-07-31	4	40.00	Josh's Spending Money	f	46	\N	7111
1055	1	2025-10-23	4	40.00	Josh's Spending Money	f	46	\N	\N
1056	1	2025-11-06	4	40.00	Josh's Spending Money	f	46	\N	\N
1057	1	2025-11-20	4	40.00	Josh's Spending Money	f	46	\N	\N
1058	1	2025-12-04	4	40.00	Josh's Spending Money	f	46	\N	\N
1059	1	2025-12-18	4	40.00	Josh's Spending Money	f	46	\N	\N
1060	1	2026-01-01	4	40.00	Josh's Spending Money	f	46	\N	\N
1061	1	2026-01-15	4	40.00	Josh's Spending Money	f	46	\N	\N
1062	1	2026-01-29	4	40.00	Josh's Spending Money	f	46	\N	\N
1063	1	2026-02-12	4	40.00	Josh's Spending Money	f	46	\N	\N
1064	1	2026-02-26	4	40.00	Josh's Spending Money	f	46	\N	\N
1065	1	2026-03-12	4	40.00	Josh's Spending Money	f	46	\N	\N
1066	1	2026-03-26	4	40.00	Josh's Spending Money	f	46	\N	\N
1067	1	2026-04-09	4	40.00	Josh's Spending Money	f	46	\N	\N
1068	1	2026-04-23	4	40.00	Josh's Spending Money	f	46	\N	\N
1069	1	2026-05-07	4	40.00	Josh's Spending Money	f	46	\N	\N
1070	1	2026-05-21	4	40.00	Josh's Spending Money	f	46	\N	\N
1071	1	2026-06-04	4	40.00	Josh's Spending Money	f	46	\N	\N
1072	1	2026-06-18	4	40.00	Josh's Spending Money	f	46	\N	\N
1073	1	2026-07-02	4	40.00	Josh's Spending Money	f	46	\N	\N
1074	1	2026-07-16	4	40.00	Josh's Spending Money	f	46	\N	\N
1075	1	2026-07-30	4	40.00	Josh's Spending Money	f	46	\N	\N
1076	1	2026-08-13	4	40.00	Josh's Spending Money	f	46	\N	\N
1077	1	2026-08-27	4	40.00	Josh's Spending Money	f	46	\N	\N
982	1	2025-09-01	4	400.00	Children's Clothes	f	44	\N	\N
1078	1	2026-09-10	4	40.00	Josh's Spending Money	f	46	\N	\N
1079	1	2026-09-24	4	40.00	Josh's Spending Money	f	46	\N	\N
1080	1	2026-10-08	4	40.00	Josh's Spending Money	f	46	\N	\N
1081	1	2026-10-22	4	40.00	Josh's Spending Money	f	46	\N	\N
1082	1	2026-11-05	4	40.00	Josh's Spending Money	f	46	\N	\N
1083	1	2026-11-19	4	40.00	Josh's Spending Money	f	46	\N	\N
1084	1	2026-12-03	4	40.00	Josh's Spending Money	f	46	\N	\N
1085	1	2026-12-17	4	40.00	Josh's Spending Money	f	46	\N	\N
1086	1	2026-12-31	4	40.00	Josh's Spending Money	f	46	\N	\N
1087	1	2027-01-14	4	40.00	Josh's Spending Money	f	46	\N	\N
1088	1	2027-01-28	4	40.00	Josh's Spending Money	f	46	\N	\N
1089	1	2027-02-11	4	40.00	Josh's Spending Money	f	46	\N	\N
1090	1	2027-02-25	4	40.00	Josh's Spending Money	f	46	\N	\N
1091	1	2027-03-11	4	40.00	Josh's Spending Money	f	46	\N	\N
1092	1	2027-03-25	4	40.00	Josh's Spending Money	f	46	\N	\N
1093	1	2027-04-08	4	40.00	Josh's Spending Money	f	46	\N	\N
1098	1	2025-05-08	4	60.00	Kayla's Spending Money	f	47	\N	7108
1099	1	2025-05-22	4	60.00	Kayla's Spending Money	f	47	\N	7109
1100	1	2025-06-05	4	60.00	Kayla's Spending Money	f	47	\N	7110
1101	1	2025-06-19	4	60.00	Kayla's Spending Money	f	47	\N	7111
1102	1	2025-07-03	4	60.00	Kayla's Spending Money	f	47	\N	7111
1103	1	2025-07-17	4	60.00	Kayla's Spending Money	f	47	\N	7111
1104	1	2025-07-31	4	60.00	Kayla's Spending Money	f	47	\N	7111
1110	1	2025-10-23	4	60.00	Kayla's Spending Money	f	47	\N	\N
1111	1	2025-11-06	4	60.00	Kayla's Spending Money	f	47	\N	\N
1112	1	2025-11-20	4	60.00	Kayla's Spending Money	f	47	\N	\N
1113	1	2025-12-04	4	60.00	Kayla's Spending Money	f	47	\N	\N
1114	1	2025-12-18	4	60.00	Kayla's Spending Money	f	47	\N	\N
1115	1	2026-01-01	4	60.00	Kayla's Spending Money	f	47	\N	\N
1116	1	2026-01-15	4	60.00	Kayla's Spending Money	f	47	\N	\N
1117	1	2026-01-29	4	60.00	Kayla's Spending Money	f	47	\N	\N
1118	1	2026-02-12	4	60.00	Kayla's Spending Money	f	47	\N	\N
1119	1	2026-02-26	4	60.00	Kayla's Spending Money	f	47	\N	\N
1120	1	2026-03-12	4	60.00	Kayla's Spending Money	f	47	\N	\N
1121	1	2026-03-26	4	60.00	Kayla's Spending Money	f	47	\N	\N
1122	1	2026-04-09	4	60.00	Kayla's Spending Money	f	47	\N	\N
1123	1	2026-04-23	4	60.00	Kayla's Spending Money	f	47	\N	\N
1124	1	2026-05-07	4	60.00	Kayla's Spending Money	f	47	\N	\N
1125	1	2026-05-21	4	60.00	Kayla's Spending Money	f	47	\N	\N
1126	1	2026-06-04	4	60.00	Kayla's Spending Money	f	47	\N	\N
1127	1	2026-06-18	4	60.00	Kayla's Spending Money	f	47	\N	\N
1128	1	2026-07-02	4	60.00	Kayla's Spending Money	f	47	\N	\N
1129	1	2026-07-16	4	60.00	Kayla's Spending Money	f	47	\N	\N
1130	1	2026-07-30	4	60.00	Kayla's Spending Money	f	47	\N	\N
1131	1	2026-08-13	4	60.00	Kayla's Spending Money	f	47	\N	\N
1132	1	2026-08-27	4	60.00	Kayla's Spending Money	f	47	\N	\N
1133	1	2026-09-10	4	60.00	Kayla's Spending Money	f	47	\N	\N
1134	1	2026-09-24	4	60.00	Kayla's Spending Money	f	47	\N	\N
1135	1	2026-10-08	4	60.00	Kayla's Spending Money	f	47	\N	\N
1136	1	2026-10-22	4	60.00	Kayla's Spending Money	f	47	\N	\N
1137	1	2026-11-05	4	60.00	Kayla's Spending Money	f	47	\N	\N
1138	1	2026-11-19	4	60.00	Kayla's Spending Money	f	47	\N	\N
1139	1	2026-12-03	4	60.00	Kayla's Spending Money	f	47	\N	\N
1140	1	2026-12-17	4	60.00	Kayla's Spending Money	f	47	\N	\N
1141	1	2026-12-31	4	60.00	Kayla's Spending Money	f	47	\N	\N
1142	1	2027-01-14	4	60.00	Kayla's Spending Money	f	47	\N	\N
1143	1	2027-01-28	4	60.00	Kayla's Spending Money	f	47	\N	\N
1144	1	2027-02-11	4	60.00	Kayla's Spending Money	f	47	\N	\N
1145	1	2027-02-25	4	60.00	Kayla's Spending Money	f	47	\N	\N
1146	1	2027-03-11	4	60.00	Kayla's Spending Money	f	47	\N	\N
1147	1	2027-03-25	4	60.00	Kayla's Spending Money	f	47	\N	\N
1148	1	2027-04-08	4	60.00	Kayla's Spending Money	f	47	\N	\N
2615	1	2027-03-11	7	1300.00	Kayla's New Phone	f	99	\N	\N
2073	1	2025-05-01	4	100.00	Kayla's Birthday	f	87	\N	7107
2057	1	2025-03-13	1	100.00	Mower Maintenance	f	81	\N	7112
62	1	2025-08-15	3	18.14	Apple Music	f	24	\N	\N
63	1	2025-09-15	3	18.14	Apple Music	f	24	\N	\N
1427	1	2025-05-15	6	234.30	Electricity	f	54	\N	7108
1428	1	2025-06-15	6	234.30	Electricity	f	54	\N	7110
1457	1	2025-05-22	6	83.00	Spectrum Internet	f	56	\N	7109
124	1	2025-07-17	5	400.00	Groceries	f	23	\N	7111
125	1	2025-07-31	5	400.00	Groceries	f	23	\N	7111
1421	1	2025-10-24	6	40.00	HVAC Air Filters	f	53	\N	\N
1422	1	2026-04-24	6	40.00	HVAC Air Filters	f	53	\N	\N
1423	1	2026-10-24	6	40.00	HVAC Air Filters	f	53	\N	\N
1424	1	2027-04-24	6	40.00	HVAC Air Filters	f	53	\N	\N
1433	1	2025-11-15	6	234.30	Electricity	f	54	\N	\N
1434	1	2025-12-15	6	234.30	Electricity	f	54	\N	\N
1435	1	2026-01-15	6	234.30	Electricity	f	54	\N	\N
1436	1	2026-02-15	6	234.30	Electricity	f	54	\N	\N
1437	1	2026-03-15	6	234.30	Electricity	f	54	\N	\N
1438	1	2026-04-15	6	234.30	Electricity	f	54	\N	\N
1439	1	2026-05-15	6	234.30	Electricity	f	54	\N	\N
1440	1	2026-06-15	6	234.30	Electricity	f	54	\N	\N
1441	1	2026-07-15	6	234.30	Electricity	f	54	\N	\N
1442	1	2026-08-15	6	234.30	Electricity	f	54	\N	\N
1443	1	2026-09-15	6	234.30	Electricity	f	54	\N	\N
1444	1	2026-10-15	6	234.30	Electricity	f	54	\N	\N
1445	1	2026-11-15	6	234.30	Electricity	f	54	\N	\N
1446	1	2026-12-15	6	234.30	Electricity	f	54	\N	\N
1447	1	2027-01-15	6	234.30	Electricity	f	54	\N	\N
1448	1	2027-02-15	6	234.30	Electricity	f	54	\N	\N
1449	1	2027-03-15	6	234.30	Electricity	f	54	\N	\N
1452	1	2026-03-13	6	120.00	House Water Filters	f	55	\N	\N
1453	1	2026-09-13	6	120.00	House Water Filters	f	55	\N	\N
1454	1	2027-03-13	6	120.00	House Water Filters	f	55	\N	\N
1462	1	2025-10-22	6	83.00	Spectrum Internet	f	56	\N	\N
1463	1	2025-11-22	6	83.00	Spectrum Internet	f	56	\N	\N
1464	1	2025-12-22	6	83.00	Spectrum Internet	f	56	\N	\N
1465	1	2026-01-22	6	83.00	Spectrum Internet	f	56	\N	\N
1466	1	2026-02-22	6	83.00	Spectrum Internet	f	56	\N	\N
1467	1	2026-03-22	6	83.00	Spectrum Internet	f	56	\N	\N
1468	1	2026-04-22	6	83.00	Spectrum Internet	f	56	\N	\N
1469	1	2026-05-22	6	83.00	Spectrum Internet	f	56	\N	\N
1470	1	2026-06-22	6	83.00	Spectrum Internet	f	56	\N	\N
1471	1	2026-07-22	6	83.00	Spectrum Internet	f	56	\N	\N
1472	1	2026-08-22	6	83.00	Spectrum Internet	f	56	\N	\N
1473	1	2026-09-22	6	83.00	Spectrum Internet	f	56	\N	\N
1474	1	2026-10-22	6	83.00	Spectrum Internet	f	56	\N	\N
1475	1	2026-11-22	6	83.00	Spectrum Internet	f	56	\N	\N
1476	1	2026-12-22	6	83.00	Spectrum Internet	f	56	\N	\N
1477	1	2027-01-22	6	83.00	Spectrum Internet	f	56	\N	\N
1478	1	2027-02-22	6	83.00	Spectrum Internet	f	56	\N	\N
1479	1	2027-03-22	6	83.00	Spectrum Internet	f	56	\N	\N
1483	1	2026-01-15	7	138.58	Mint Mobile	f	57	\N	\N
1484	1	2026-04-15	7	138.58	Mint Mobile	f	57	\N	\N
1485	1	2026-07-15	7	138.58	Mint Mobile	f	57	\N	\N
1486	1	2026-10-15	7	138.58	Mint Mobile	f	57	\N	\N
1487	1	2027-01-15	7	138.58	Mint Mobile	f	57	\N	\N
1488	1	2027-04-15	7	138.58	Mint Mobile	f	57	\N	\N
76	1	2025-08-28	1	80.00	Gas	f	21	\N	\N
77	1	2025-09-11	1	80.00	Gas	f	21	\N	\N
78	1	2025-09-25	1	80.00	Gas	f	21	\N	\N
79	1	2025-10-09	1	80.00	Gas	f	21	\N	\N
126	1	2025-08-14	5	400.00	Groceries	f	23	\N	\N
1429	1	2025-07-15	6	234.30	Electricity	f	54	\N	7111
1459	1	2025-07-22	6	83.00	Spectrum Internet	f	56	\N	7111
1481	1	2025-07-15	7	138.58	Mint Mobile	f	57	\N	7111
1546	1	2026-03-13	6	35.00	Refrigerator Filter	f	60	\N	\N
1547	1	2026-09-13	6	35.00	Refrigerator Filter	f	60	\N	\N
1548	1	2027-03-13	6	35.00	Refrigerator Filter	f	60	\N	\N
1430	1	2025-08-15	6	234.30	Electricity	f	54	\N	\N
1431	1	2025-09-15	6	234.30	Electricity	f	54	\N	\N
1432	1	2025-10-15	6	234.30	Electricity	f	54	\N	\N
1451	1	2025-09-13	6	120.00	House Water Filters	f	55	\N	\N
1460	1	2025-08-22	6	83.00	Spectrum Internet	f	56	\N	\N
1461	1	2025-09-22	6	83.00	Spectrum Internet	f	56	\N	\N
1482	1	2025-10-15	7	138.58	Mint Mobile	f	57	\N	\N
1458	1	2025-06-22	6	83.00	Spectrum Internet	f	56	\N	7111
1660	1	2025-05-22	6	30.00	Toilet Paper	f	63	\N	7109
1661	1	2025-06-19	6	30.00	Toilet Paper	f	63	\N	7111
73	1	2025-07-17	1	80.00	Gas	f	21	\N	7111
64	1	2025-10-15	3	18.14	Apple Music	f	24	\N	\N
75	1	2025-08-14	1	80.00	Gas	f	21	\N	\N
127	1	2025-08-28	5	400.00	Groceries	f	23	\N	\N
128	1	2025-09-11	5	400.00	Groceries	f	23	\N	\N
129	1	2025-09-25	5	400.00	Groceries	f	23	\N	\N
130	1	2025-10-09	5	400.00	Groceries	f	23	\N	\N
189	1	2025-08-20	1	531.94	Van Payment	f	25	\N	\N
190	1	2025-09-20	1	531.94	Van Payment	f	25	\N	\N
191	1	2025-10-20	1	531.94	Van Payment	f	25	\N	\N
236	1	2025-08-01	1	183.49	Geico Car Insurance	f	26	\N	\N
237	1	2025-09-01	1	183.49	Geico Car Insurance	f	26	\N	\N
238	1	2025-10-01	1	183.49	Geico Car Insurance	f	26	\N	\N
312	1	2025-10-11	1	175.00	Oil & Air Filters	f	28	\N	\N
554	1	2025-08-26	3	15.96	Audible	f	34	\N	\N
555	1	2025-09-26	3	15.96	Audible	f	34	\N	\N
579	1	2025-08-26	3	17.15	Disney+	f	35	\N	\N
580	1	2025-09-26	3	17.15	Disney+	f	35	\N	\N
604	1	2025-08-17	7	9.99	iCloud 2TB	f	36	\N	\N
605	1	2025-09-17	7	9.99	iCloud 2TB	f	36	\N	\N
606	1	2025-10-17	7	9.99	iCloud 2TB	f	36	\N	\N
1050	1	2025-08-14	4	40.00	Josh's Spending Money	f	46	\N	\N
1051	1	2025-08-28	4	40.00	Josh's Spending Money	f	46	\N	\N
1052	1	2025-09-11	4	40.00	Josh's Spending Money	f	46	\N	\N
1053	1	2025-09-25	4	40.00	Josh's Spending Money	f	46	\N	\N
1054	1	2025-10-09	4	40.00	Josh's Spending Money	f	46	\N	\N
1105	1	2025-08-14	4	60.00	Kayla's Spending Money	f	47	\N	\N
1106	1	2025-08-28	4	60.00	Kayla's Spending Money	f	47	\N	\N
1107	1	2025-09-11	4	60.00	Kayla's Spending Money	f	47	\N	\N
1108	1	2025-09-25	4	60.00	Kayla's Spending Money	f	47	\N	\N
1109	1	2025-10-09	4	60.00	Kayla's Spending Money	f	47	\N	\N
1545	1	2025-09-13	6	35.00	Refrigerator Filter	f	60	\N	\N
1663	1	2025-08-14	6	30.00	Toilet Paper	f	63	\N	\N
1664	1	2025-09-11	6	30.00	Toilet Paper	f	63	\N	\N
1665	1	2025-10-09	6	30.00	Toilet Paper	f	63	\N	\N
2060	1	2025-10-11	1	122.98	RAV4 Property Tax	f	82	\N	\N
2063	1	2025-10-11	1	30.00	RAV4 State Inspection	f	83	\N	\N
74	1	2025-07-31	1	80.00	Gas	f	21	\N	7111
188	1	2025-07-20	1	531.94	Van Payment	f	25	\N	7111
578	1	2025-07-26	3	17.15	Disney+	f	35	\N	7111
603	1	2025-07-17	7	9.99	iCloud 2TB	f	36	\N	7111
1666	1	2025-11-06	6	30.00	Toilet Paper	f	63	\N	\N
1667	1	2025-12-04	6	30.00	Toilet Paper	f	63	\N	\N
1668	1	2026-01-01	6	30.00	Toilet Paper	f	63	\N	\N
1669	1	2026-01-29	6	30.00	Toilet Paper	f	63	\N	\N
1670	1	2026-02-26	6	30.00	Toilet Paper	f	63	\N	\N
1671	1	2026-03-26	6	30.00	Toilet Paper	f	63	\N	\N
1672	1	2026-04-23	6	30.00	Toilet Paper	f	63	\N	\N
1673	1	2026-05-21	6	30.00	Toilet Paper	f	63	\N	\N
1674	1	2026-06-18	6	30.00	Toilet Paper	f	63	\N	\N
1675	1	2026-07-16	6	30.00	Toilet Paper	f	63	\N	\N
1676	1	2026-08-13	6	30.00	Toilet Paper	f	63	\N	\N
1677	1	2026-09-10	6	30.00	Toilet Paper	f	63	\N	\N
1678	1	2026-10-08	6	30.00	Toilet Paper	f	63	\N	\N
1679	1	2026-11-05	6	30.00	Toilet Paper	f	63	\N	\N
1680	1	2026-12-03	6	30.00	Toilet Paper	f	63	\N	\N
1681	1	2026-12-31	6	30.00	Toilet Paper	f	63	\N	\N
1682	1	2027-01-28	6	30.00	Toilet Paper	f	63	\N	\N
1683	1	2027-02-25	6	30.00	Toilet Paper	f	63	\N	\N
1684	1	2027-03-25	6	30.00	Toilet Paper	f	63	\N	\N
1662	1	2025-07-17	6	30.00	Toilet Paper	f	63	\N	7111
1685	1	2025-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	7109
1687	1	2025-12-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1688	1	2026-03-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1689	1	2026-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1690	1	2026-09-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1691	1	2026-12-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1692	1	2027-03-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1693	1	2027-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1686	1	2025-09-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
67	1	2025-04-24	1	80.00	Gas	f	21	\N	7107
184	1	2025-03-20	1	531.94	Van Payment	f	25	\N	7104
185	1	2025-04-20	1	531.94	Van Payment	f	25	\N	7106
232	1	2025-04-01	1	183.49	Geico Car Insurance	f	26	\N	7105
233	1	2025-05-01	1	183.49	Geico Car Insurance	f	26	\N	7107
311	1	2025-04-11	1	175.00	Oil & Air Filters	f	28	\N	7106
549	1	2025-03-26	3	15.96	Audible	f	34	\N	7104
550	1	2025-04-26	3	15.96	Audible	f	34	\N	7107
574	1	2025-03-26	3	17.15	Disney+	f	35	\N	7104
575	1	2025-04-26	3	17.15	Disney+	f	35	\N	7107
599	1	2025-03-17	7	9.99	iCloud 2TB	f	36	\N	7104
600	1	2025-04-17	7	9.99	iCloud 2TB	f	36	\N	7106
1041	1	2025-04-10	4	40.00	Josh's Spending Money	f	46	\N	7106
1042	1	2025-04-24	4	40.00	Josh's Spending Money	f	46	\N	7107
1094	1	2025-03-13	4	60.00	Kayla's Spending Money	f	47	\N	7104
1095	1	2025-03-27	4	60.00	Kayla's Spending Money	f	47	\N	7105
1096	1	2025-04-10	4	60.00	Kayla's Spending Money	f	47	\N	7106
1097	1	2025-04-24	4	60.00	Kayla's Spending Money	f	47	\N	7107
1420	1	2025-04-24	6	40.00	HVAC Air Filters	f	53	\N	7107
1425	1	2025-03-15	6	234.30	Electricity	f	54	\N	7104
1426	1	2025-04-15	6	234.30	Electricity	f	54	\N	7106
1455	1	2025-03-22	6	83.00	Spectrum Internet	f	56	\N	7104
1456	1	2025-04-22	6	83.00	Spectrum Internet	f	56	\N	7106
1480	1	2025-04-15	7	138.58	Mint Mobile	f	57	\N	7106
1544	1	2025-03-13	6	35.00	Refrigerator Filter	f	60	\N	7104
1658	1	2025-03-27	6	30.00	Toilet Paper	f	63	\N	7105
1659	1	2025-04-24	6	30.00	Toilet Paper	f	63	\N	7107
1450	1	2025-03-13	6	120.00	House Water Filters	f	55	\N	7112
\.


--
-- TOC entry 3564 (class 0 OID 16419)
-- Dependencies: 224
-- Data for Name: frequencies; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.frequencies (id, name, description) FROM stdin;
1	Biweekly	Biweekly payments/expenses
2	Monthly	Once a month
4	Quarterly	Every 3 months
5	Weekly	Every week
6	Semimonthly	Twice per month
3	Annually	Once a year
\.


--
-- TOC entry 3566 (class 0 OID 16430)
-- Dependencies: 226
-- Data for Name: income_categories; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.income_categories (id, name, description, color, icon) FROM stdin;
1	Salary	Regular Salary	#0a6901	M20 7h-4V3.5A1.5 1.5 0 0 0 14.5 2h-5A1.5 1.5 0 0 0 8 3.5V7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2z M16 21V11 M8 21V11
4	Third Pay	Third monthly paycheck	#26a269	M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6
2	Bonus	Bonus	#62a0ea	M20 12v10H4V12M2 7h20v5H2zM12 22V7M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7zM12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z
3	Phone Stipend	Monthly phone stipend	#f8e45c	M16.5 10l-5.5 3v-6l5.5 3Z M12 19c-5 0-8-3-8-3V8s3-3 8-3 8 3 8 3v8s-3 3-8 3Z
5	Tax Return	Taxation is theft	#0a6901	M3 21h18M3 10h18M3 7l9-4 9 4M4 10v11M20 10v11M8 14v7M12 14v7M16 14v7
6	Longevity Check		#0a6901	M22 12h-4l-3 9L9 3l-3 9H2
\.


--
-- TOC entry 3584 (class 0 OID 16573)
-- Dependencies: 244
-- Data for Name: income_payments; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.income_payments (id, paycheck_id, account_id, payment_date, amount, is_percentage, percentage) FROM stdin;
7085	7104	1	2025-03-13	2369.04	t	100.00
7086	7105	1	2025-03-27	2369.04	t	100.00
7087	7106	1	2025-04-10	2369.04	t	100.00
7088	7107	1	2025-04-24	2369.04	t	100.00
7089	7108	1	2025-05-08	2369.04	t	100.00
7090	7109	1	2025-05-22	2369.04	t	100.00
7091	7110	1	2025-06-05	2369.04	t	100.00
7092	7111	1	2025-06-19	2369.04	t	100.00
7093	7112	1	2025-03-13	895.00	f	\N
\.


--
-- TOC entry 3578 (class 0 OID 16515)
-- Dependencies: 238
-- Data for Name: paychecks; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.paychecks (id, user_id, scheduled_date, gross_salary, taxes, deductions, net_salary, is_projected, category_id, recurring_schedule_id, paid) FROM stdin;
7104	1	2025-03-13	3372.50	182.12	821.35	2369.04	t	\N	116	f
7105	1	2025-03-27	3372.50	182.12	821.35	2369.04	t	\N	116	f
7106	1	2025-04-10	3372.50	182.12	821.35	2369.04	t	\N	116	f
7107	1	2025-04-24	3372.50	182.12	821.35	2369.04	t	\N	116	f
7108	1	2025-05-08	3372.50	182.12	821.35	2369.04	t	\N	116	f
7109	1	2025-05-22	3372.50	182.12	821.35	2369.04	t	\N	116	f
7110	1	2025-06-05	3372.50	182.12	821.35	2369.04	t	\N	116	f
7111	1	2025-06-19	3372.50	182.12	821.35	2369.04	t	\N	116	f
7112	1	2025-03-13	895.00	0.00	0.00	895.00	f	5	117	t
\.


--
-- TOC entry 3574 (class 0 OID 16481)
-- Dependencies: 234
-- Data for Name: recurring_schedules; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.recurring_schedules (id, user_id, type_id, description, frequency_id, "interval", start_date, end_date, amount, category_type, category_id, default_account_id) FROM stdin;
108	1	2	Emergency Fund	1	1	2025-03-13	2027-04-01	500.00	expense	8	1
117	1	1	State Tax Return	\N	1	2025-03-13	2025-03-13	895.00	\N	\N	\N
113	1	1	Salary - $87,685.00/year (Combined) (Combined) (Combined) (Combined) (Combined)	\N	1	2025-03-13	2025-06-30	3372.50	\N	\N	\N
116	1	1	Salary - $87,685.00/year	\N	1	2025-03-13	2025-06-30	3372.50	\N	\N	\N
67	1	1	State Tax Return (Combined) (Combined) (Combined) (Combined)	\N	1	2025-03-14	2025-03-14	895.00	\N	\N	\N
132	1	2	Proton Family	3	1	2025-08-13	2027-08-31	288.00	expense	7	1
13	1	1	Salary - 87685.00/year	1	1	2025-02-27	2025-06-30	3372.50	income	\N	\N
122	1	2	Josh's Birthday	3	1	2025-03-24	2027-03-27	100.00	expense	4	1
123	1	2	Father's Day	3	1	2025-06-08	2027-06-30	100.00	expense	4	1
124	1	2	Mother's Day	3	1	2025-05-04	2027-05-30	100.00	expense	4	1
125	1	2	Strawberry Picking	3	1	2025-04-10	2027-04-11	200.00	expense	5	1
126	1	2	New Baby Birthday	3	1	2025-08-21	2027-08-30	100.00	expense	4	1
127	1	2	Ariella's Birthday	3	1	2025-10-07	2027-11-30	100.00	expense	4	1
128	1	2	Christmas	3	1	2025-11-01	2027-12-31	400.00	expense	4	1
21	1	2	Gas	1	1	2025-03-13	2027-05-01	80.00	expense	1	1
23	1	2	Groceries	1	1	2025-03-13	2027-03-31	400.00	expense	5	1
24	1	2	Apple Music	2	1	2025-03-15	2027-03-31	18.14	expense	3	1
25	1	2	Van Payment	2	1	2025-03-20	2029-03-01	531.94	expense	1	1
26	1	2	Geico Car Insurance	2	1	2025-04-01	2027-04-01	183.49	expense	1	1
129	1	2	School Curriculum	3	1	2025-07-01	2027-09-01	600.00	expense	4	1
28	1	2	Oil & Air Filters	2	6	2025-04-11	2027-04-20	175.00	expense	1	1
130	1	2	Wedding Anniversary	3	1	2025-10-18	2027-11-30	100.00	expense	4	1
131	1	2	Josh's New Phone	3	2	2025-11-13	2027-11-30	1300.00	expense	7	1
133	1	2	BJ's Club Membership	3	1	2026-03-01	2027-04-01	55.00	expense	5	1
134	1	2	Sam's Club Membership	3	1	2025-03-20	2027-04-01	55.00	expense	5	1
68	1	1	Federal Tax Return	\N	1	2026-03-08	2026-03-08	3142.00	\N	\N	\N
135	1	2	Knox's Birthday	3	1	2026-02-22	2027-03-01	100.00	expense	4	1
34	1	2	Audible	2	1	2025-03-26	2027-04-08	15.96	expense	3	1
35	1	2	Disney+	2	1	2025-03-26	2027-04-08	17.15	expense	3	1
36	1	2	iCloud 2TB	2	1	2025-03-17	2027-04-08	9.99	expense	7	1
136	1	2	Valentine's Day	3	1	2026-02-14	2026-03-01	100.00	expense	4	1
137	1	2	Mortgage	2	1	2025-04-01	2026-12-31	1670.20	expense	6	1
44	1	2	Children's Clothes	2	6	2025-09-01	2027-10-01	400.00	expense	4	1
46	1	2	Josh's Spending Money	1	1	2025-04-10	2027-04-11	40.00	expense	4	1
47	1	2	Kayla's Spending Money	1	1	2025-03-13	2027-04-08	60.00	expense	4	1
53	1	2	HVAC Air Filters	2	6	2025-04-24	2027-04-30	40.00	expense	6	\N
54	1	2	Electricity	2	1	2025-03-15	2027-04-08	234.30	expense	6	1
55	1	2	House Water Filters	2	6	2025-03-13	2027-04-08	120.00	expense	6	1
56	1	2	Spectrum Internet	2	1	2025-03-22	2027-04-09	83.00	expense	6	1
57	1	2	Mint Mobile	4	1	2025-04-15	2027-04-16	138.58	expense	7	1
60	1	2	Refrigerator Filter	2	6	2025-03-13	2027-04-08	35.00	expense	6	1
63	1	2	Toilet Paper	1	2	2025-03-27	2027-04-08	30.00	expense	6	1
64	1	2	Anchor Trash Pickup	4	1	2025-06-01	2027-07-01	61.50	expense	6	1
69	1	1	State Tax Return	\N	1	2026-03-16	2026-03-16	895.00	\N	\N	\N
70	1	1	State Tax Return	\N	1	2027-03-15	2027-03-15	895.00	\N	\N	\N
71	1	1	Federal Tax Return	\N	1	2027-03-08	2027-03-08	3142.00	\N	\N	\N
72	1	1	Longevity Check	\N	1	2025-11-13	2025-11-13	277.05	\N	\N	\N
73	1	1	Longevity Check	\N	1	2026-11-12	2026-11-12	369.40	\N	\N	\N
81	1	2	Mower Maintenance	3	1	2025-03-13	2027-03-31	100.00	expense	1	1
82	1	2	RAV4 Property Tax	3	1	2025-10-11	2027-11-01	122.98	expense	1	1
83	1	2	RAV4 State Inspection	3	1	2025-10-11	2027-10-31	30.00	expense	1	1
84	1	2	Van State Inspection	3	1	2026-01-11	2027-03-31	30.00	expense	1	1
85	1	2	Van Property Tax	3	1	2025-01-11	2027-03-31	208.17	expense	1	1
86	1	2	Eliana's Birthday	3	1	2026-01-08	2027-02-01	100.00	expense	4	1
87	1	2	Kayla's Birthday	3	1	2025-05-01	2027-05-02	100.00	expense	4	1
99	1	2	Kayla's New Phone	3	2	2027-03-11	2027-04-01	1300.00	expense	7	1
\.


--
-- TOC entry 3558 (class 0 OID 16390)
-- Dependencies: 218
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.roles (id, name, description) FROM stdin;
1	ADMIN	Administrator role
\.


--
-- TOC entry 3576 (class 0 OID 16503)
-- Dependencies: 236
-- Data for Name: salary_changes; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.salary_changes (id, user_id, effective_date, end_date, gross_annual_salary, federal_tax_rate, state_tax_rate, retirement_contribution_rate, health_insurance_amount, other_deductions_amount, notes) FROM stdin;
17	1	2025-03-13	2025-06-30	87685.00	4.00	1.40	6.00	249.00	370.00	
\.


--
-- TOC entry 3592 (class 0 OID 16639)
-- Dependencies: 252
-- Data for Name: salary_deposit_allocations; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.salary_deposit_allocations (id, salary_id, account_id, is_percentage, percentage, amount) FROM stdin;
16	17	1	t	100.00	\N
\.


--
-- TOC entry 3562 (class 0 OID 16408)
-- Dependencies: 222
-- Data for Name: schedule_types; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.schedule_types (id, name, description) FROM stdin;
1	income	Regular income
2	expense	Expense
\.


--
-- TOC entry 3588 (class 0 OID 16608)
-- Dependencies: 248
-- Data for Name: transactions; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.transactions (id, account_id, transaction_date, amount, description, transaction_type, related_transaction_id) FROM stdin;
6	4	2025-03-01	0.13	Interest accrual (20.00% daily)	deposit	\N
14	1	2025-03-13	1551.78	Expense 2945: Credit Card	withdrawal	\N
\.


--
-- TOC entry 3594 (class 0 OID 16683)
-- Dependencies: 254
-- Data for Name: user_preferences; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.user_preferences (id, user_id, preference_key, preference_value) FROM stdin;
1	1	default_expense_account	1
\.


--
-- TOC entry 3570 (class 0 OID 16448)
-- Dependencies: 230
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.users (id, username, password_hash, email, role_id, first_name, last_name) FROM stdin;
1	josh	scrypt:32768:8:1$WP5YzwEHpheGD7Ox$4200d87d5da1418a09789ee0a9629f24ff504602c0558d0c6d25eda5b3ce6072a5e3cdb041e666b6edaf7f96e8fd59b5249e969ef8e7472570a0f0761d8fc756	grubbj@pm.me	1	Josh	Grubb
\.


--
-- TOC entry 3629 (class 0 OID 0)
-- Dependencies: 249
-- Name: account_interest_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.account_interest_id_seq', 3, true);


--
-- TOC entry 3630 (class 0 OID 0)
-- Dependencies: 219
-- Name: account_types_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.account_types_id_seq', 17, true);


--
-- TOC entry 3631 (class 0 OID 0)
-- Dependencies: 231
-- Name: accounts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.accounts_id_seq', 7, true);


--
-- TOC entry 3632 (class 0 OID 0)
-- Dependencies: 227
-- Name: expense_categories_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_categories_id_seq', 8, true);


--
-- TOC entry 3633 (class 0 OID 0)
-- Dependencies: 241
-- Name: expense_changes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_changes_id_seq', 1, false);


--
-- TOC entry 3634 (class 0 OID 0)
-- Dependencies: 245
-- Name: expense_payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_payments_id_seq', 6, true);


--
-- TOC entry 3635 (class 0 OID 0)
-- Dependencies: 239
-- Name: expenses_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expenses_id_seq', 2966, true);


--
-- TOC entry 3636 (class 0 OID 0)
-- Dependencies: 223
-- Name: frequencies_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.frequencies_id_seq', 7, true);


--
-- TOC entry 3637 (class 0 OID 0)
-- Dependencies: 225
-- Name: income_categories_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.income_categories_id_seq', 6, true);


--
-- TOC entry 3638 (class 0 OID 0)
-- Dependencies: 243
-- Name: income_payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.income_payments_id_seq', 7093, true);


--
-- TOC entry 3639 (class 0 OID 0)
-- Dependencies: 237
-- Name: paychecks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.paychecks_id_seq', 7112, true);


--
-- TOC entry 3640 (class 0 OID 0)
-- Dependencies: 233
-- Name: recurring_schedules_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.recurring_schedules_id_seq', 137, true);


--
-- TOC entry 3641 (class 0 OID 0)
-- Dependencies: 217
-- Name: roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.roles_id_seq', 1, true);


--
-- TOC entry 3642 (class 0 OID 0)
-- Dependencies: 235
-- Name: salary_changes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.salary_changes_id_seq', 17, true);


--
-- TOC entry 3643 (class 0 OID 0)
-- Dependencies: 251
-- Name: salary_deposit_allocations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.salary_deposit_allocations_id_seq', 16, true);


--
-- TOC entry 3644 (class 0 OID 0)
-- Dependencies: 221
-- Name: schedule_types_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.schedule_types_id_seq', 2, true);


--
-- TOC entry 3645 (class 0 OID 0)
-- Dependencies: 247
-- Name: transactions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.transactions_id_seq', 14, true);


--
-- TOC entry 3646 (class 0 OID 0)
-- Dependencies: 253
-- Name: user_preferences_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.user_preferences_id_seq', 1, true);


--
-- TOC entry 3647 (class 0 OID 0)
-- Dependencies: 229
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.users_id_seq', 1, true);


--
-- TOC entry 3377 (class 2606 OID 16630)
-- Name: account_interest account_interest_account_id_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_account_id_key UNIQUE (account_id);


--
-- TOC entry 3379 (class 2606 OID 16628)
-- Name: account_interest account_interest_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_pkey PRIMARY KEY (id);


--
-- TOC entry 3338 (class 2606 OID 16406)
-- Name: account_types account_types_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_types
    ADD CONSTRAINT account_types_pkey PRIMARY KEY (id);


--
-- TOC entry 3356 (class 2606 OID 16469)
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- TOC entry 3350 (class 2606 OID 16446)
-- Name: expense_categories expense_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_categories
    ADD CONSTRAINT expense_categories_pkey PRIMARY KEY (id);


--
-- TOC entry 3369 (class 2606 OID 16566)
-- Name: expense_changes expense_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes
    ADD CONSTRAINT expense_changes_pkey PRIMARY KEY (id);


--
-- TOC entry 3373 (class 2606 OID 16595)
-- Name: expense_payments expense_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_pkey PRIMARY KEY (id);


--
-- TOC entry 3366 (class 2606 OID 16544)
-- Name: expenses expenses_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_pkey PRIMARY KEY (id);


--
-- TOC entry 3344 (class 2606 OID 16428)
-- Name: frequencies frequencies_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies
    ADD CONSTRAINT frequencies_name_key UNIQUE (name);


--
-- TOC entry 3346 (class 2606 OID 16426)
-- Name: frequencies frequencies_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies
    ADD CONSTRAINT frequencies_pkey PRIMARY KEY (id);


--
-- TOC entry 3348 (class 2606 OID 16437)
-- Name: income_categories income_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_categories
    ADD CONSTRAINT income_categories_pkey PRIMARY KEY (id);


--
-- TOC entry 3371 (class 2606 OID 16578)
-- Name: income_payments income_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_pkey PRIMARY KEY (id);


--
-- TOC entry 3364 (class 2606 OID 16520)
-- Name: paychecks paychecks_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_pkey PRIMARY KEY (id);


--
-- TOC entry 3360 (class 2606 OID 16486)
-- Name: recurring_schedules recurring_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_pkey PRIMARY KEY (id);


--
-- TOC entry 3334 (class 2606 OID 16399)
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- TOC entry 3336 (class 2606 OID 16397)
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- TOC entry 3362 (class 2606 OID 16508)
-- Name: salary_changes salary_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes
    ADD CONSTRAINT salary_changes_pkey PRIMARY KEY (id);


--
-- TOC entry 3382 (class 2606 OID 16644)
-- Name: salary_deposit_allocations salary_deposit_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_pkey PRIMARY KEY (id);


--
-- TOC entry 3340 (class 2606 OID 16417)
-- Name: schedule_types schedule_types_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types
    ADD CONSTRAINT schedule_types_name_key UNIQUE (name);


--
-- TOC entry 3342 (class 2606 OID 16415)
-- Name: schedule_types schedule_types_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types
    ADD CONSTRAINT schedule_types_pkey PRIMARY KEY (id);


--
-- TOC entry 3375 (class 2606 OID 16613)
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- TOC entry 3384 (class 2606 OID 16690)
-- Name: user_preferences uix_user_preference; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT uix_user_preference UNIQUE (user_id, preference_key);


--
-- TOC entry 3386 (class 2606 OID 16688)
-- Name: user_preferences user_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT user_preferences_pkey PRIMARY KEY (id);


--
-- TOC entry 3352 (class 2606 OID 16455)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 3354 (class 2606 OID 16457)
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- TOC entry 3380 (class 1259 OID 16636)
-- Name: idx_account_interest_account_id; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_account_interest_account_id ON public.account_interest USING btree (account_id);


--
-- TOC entry 3367 (class 1259 OID 16672)
-- Name: idx_expenses_paycheck_id; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_expenses_paycheck_id ON public.expenses USING btree (paycheck_id);


--
-- TOC entry 3357 (class 1259 OID 16661)
-- Name: idx_recurring_schedules_account; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_recurring_schedules_account ON public.recurring_schedules USING btree (default_account_id);


--
-- TOC entry 3358 (class 1259 OID 16660)
-- Name: idx_recurring_schedules_category; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_recurring_schedules_category ON public.recurring_schedules USING btree (category_type, category_id);


--
-- TOC entry 3408 (class 2606 OID 16631)
-- Name: account_interest account_interest_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- TOC entry 3388 (class 2606 OID 16475)
-- Name: accounts accounts_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.account_types(id);


--
-- TOC entry 3389 (class 2606 OID 16470)
-- Name: accounts accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3402 (class 2606 OID 16567)
-- Name: expense_changes expense_changes_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes
    ADD CONSTRAINT expense_changes_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3405 (class 2606 OID 16601)
-- Name: expense_payments expense_payments_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3406 (class 2606 OID 16596)
-- Name: expense_payments expense_payments_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id);


--
-- TOC entry 3398 (class 2606 OID 16550)
-- Name: expenses expenses_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.expense_categories(id);


--
-- TOC entry 3399 (class 2606 OID 16555)
-- Name: expenses expenses_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3400 (class 2606 OID 16545)
-- Name: expenses expenses_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3401 (class 2606 OID 16667)
-- Name: expenses fk_expenses_paycheck; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT fk_expenses_paycheck FOREIGN KEY (paycheck_id) REFERENCES public.paychecks(id) ON DELETE SET NULL;


--
-- TOC entry 3403 (class 2606 OID 16584)
-- Name: income_payments income_payments_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3404 (class 2606 OID 16579)
-- Name: income_payments income_payments_paycheck_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_paycheck_id_fkey FOREIGN KEY (paycheck_id) REFERENCES public.paychecks(id);


--
-- TOC entry 3395 (class 2606 OID 16526)
-- Name: paychecks paychecks_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.income_categories(id);


--
-- TOC entry 3396 (class 2606 OID 16531)
-- Name: paychecks paychecks_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3397 (class 2606 OID 16521)
-- Name: paychecks paychecks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3390 (class 2606 OID 16655)
-- Name: recurring_schedules recurring_schedules_default_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_default_account_id_fkey FOREIGN KEY (default_account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3391 (class 2606 OID 16497)
-- Name: recurring_schedules recurring_schedules_frequency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_frequency_id_fkey FOREIGN KEY (frequency_id) REFERENCES public.frequencies(id);


--
-- TOC entry 3392 (class 2606 OID 16492)
-- Name: recurring_schedules recurring_schedules_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.schedule_types(id);


--
-- TOC entry 3393 (class 2606 OID 16487)
-- Name: recurring_schedules recurring_schedules_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3394 (class 2606 OID 16509)
-- Name: salary_changes salary_changes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes
    ADD CONSTRAINT salary_changes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3409 (class 2606 OID 16650)
-- Name: salary_deposit_allocations salary_deposit_allocations_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3410 (class 2606 OID 16645)
-- Name: salary_deposit_allocations salary_deposit_allocations_salary_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_salary_id_fkey FOREIGN KEY (salary_id) REFERENCES public.salary_changes(id);


--
-- TOC entry 3407 (class 2606 OID 16614)
-- Name: transactions transactions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3411 (class 2606 OID 16691)
-- Name: user_preferences user_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT user_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3387 (class 2606 OID 16458)
-- Name: users users_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id);


-- Completed on 2025-03-12 23:20:22 EDT

--
-- PostgreSQL database dump complete
--

-- Completed on 2025-03-12 23:20:22 EDT

--
-- PostgreSQL database cluster dump complete
--

