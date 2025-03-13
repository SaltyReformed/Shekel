--
-- PostgreSQL database cluster dump
--

-- Started on 2025-03-12 20:39:11 EDT

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

-- Started on 2025-03-12 20:39:11 EDT

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

-- Completed on 2025-03-12 20:39:11 EDT

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

-- Started on 2025-03-12 20:39:11 EDT

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
-- TOC entry 3587 (class 1262 OID 16384)
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
-- TOC entry 3588 (class 0 OID 0)
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
-- TOC entry 3589 (class 0 OID 0)
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
-- TOC entry 3590 (class 0 OID 0)
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
-- TOC entry 3591 (class 0 OID 0)
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
-- TOC entry 3592 (class 0 OID 0)
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
-- TOC entry 3593 (class 0 OID 0)
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
-- TOC entry 3594 (class 0 OID 0)
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
-- TOC entry 3595 (class 0 OID 0)
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
-- TOC entry 3596 (class 0 OID 0)
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
-- TOC entry 3597 (class 0 OID 0)
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
-- TOC entry 3598 (class 0 OID 0)
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
-- TOC entry 3599 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN recurring_schedules.category_type; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.recurring_schedules.category_type IS 'Type of category - either "income" or "expense"';


--
-- TOC entry 3600 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN recurring_schedules.category_id; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.recurring_schedules.category_id IS 'ID of the category (references income_categories or expense_categories depending on category_type)';


--
-- TOC entry 3601 (class 0 OID 0)
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
-- TOC entry 3602 (class 0 OID 0)
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
-- TOC entry 3603 (class 0 OID 0)
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
-- TOC entry 3604 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.federal_tax_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.federal_tax_rate IS 'Federal tax rate as a percentage';


--
-- TOC entry 3605 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.state_tax_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.state_tax_rate IS 'State tax rate as a percentage';


--
-- TOC entry 3606 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.retirement_contribution_rate; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.retirement_contribution_rate IS 'Retirement contribution rate as a percentage';


--
-- TOC entry 3607 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.health_insurance_amount; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.health_insurance_amount IS 'Health insurance amount per paycheck';


--
-- TOC entry 3608 (class 0 OID 0)
-- Dependencies: 236
-- Name: COLUMN salary_changes.other_deductions_amount; Type: COMMENT; Schema: public; Owner: grubb
--

COMMENT ON COLUMN public.salary_changes.other_deductions_amount IS 'Other deductions amount per paycheck';


--
-- TOC entry 3609 (class 0 OID 0)
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
-- TOC entry 3610 (class 0 OID 0)
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
-- TOC entry 3611 (class 0 OID 0)
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
-- TOC entry 3612 (class 0 OID 0)
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
-- TOC entry 3613 (class 0 OID 0)
-- Dependencies: 247
-- Name: transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.transactions_id_seq OWNED BY public.transactions.id;


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
-- TOC entry 3614 (class 0 OID 0)
-- Dependencies: 229
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: grubb
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- TOC entry 3322 (class 2604 OID 16623)
-- Name: account_interest id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest ALTER COLUMN id SET DEFAULT nextval('public.account_interest_id_seq'::regclass);


--
-- TOC entry 3296 (class 2604 OID 16404)
-- Name: account_types id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_types ALTER COLUMN id SET DEFAULT nextval('public.account_types_id_seq'::regclass);


--
-- TOC entry 3307 (class 2604 OID 16467)
-- Name: accounts id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);


--
-- TOC entry 3302 (class 2604 OID 16442)
-- Name: expense_categories id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_categories ALTER COLUMN id SET DEFAULT nextval('public.expense_categories_id_seq'::regclass);


--
-- TOC entry 3317 (class 2604 OID 16564)
-- Name: expense_changes id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes ALTER COLUMN id SET DEFAULT nextval('public.expense_changes_id_seq'::regclass);


--
-- TOC entry 3320 (class 2604 OID 16593)
-- Name: expense_payments id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments ALTER COLUMN id SET DEFAULT nextval('public.expense_payments_id_seq'::regclass);


--
-- TOC entry 3316 (class 2604 OID 16540)
-- Name: expenses id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses ALTER COLUMN id SET DEFAULT nextval('public.expenses_id_seq'::regclass);


--
-- TOC entry 3298 (class 2604 OID 16422)
-- Name: frequencies id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies ALTER COLUMN id SET DEFAULT nextval('public.frequencies_id_seq'::regclass);


--
-- TOC entry 3299 (class 2604 OID 16433)
-- Name: income_categories id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_categories ALTER COLUMN id SET DEFAULT nextval('public.income_categories_id_seq'::regclass);


--
-- TOC entry 3318 (class 2604 OID 16576)
-- Name: income_payments id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments ALTER COLUMN id SET DEFAULT nextval('public.income_payments_id_seq'::regclass);


--
-- TOC entry 3315 (class 2604 OID 16518)
-- Name: paychecks id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks ALTER COLUMN id SET DEFAULT nextval('public.paychecks_id_seq'::regclass);


--
-- TOC entry 3308 (class 2604 OID 16484)
-- Name: recurring_schedules id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules ALTER COLUMN id SET DEFAULT nextval('public.recurring_schedules_id_seq'::regclass);


--
-- TOC entry 3295 (class 2604 OID 16393)
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- TOC entry 3309 (class 2604 OID 16506)
-- Name: salary_changes id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes ALTER COLUMN id SET DEFAULT nextval('public.salary_changes_id_seq'::regclass);


--
-- TOC entry 3326 (class 2604 OID 16642)
-- Name: salary_deposit_allocations id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations ALTER COLUMN id SET DEFAULT nextval('public.salary_deposit_allocations_id_seq'::regclass);


--
-- TOC entry 3297 (class 2604 OID 16411)
-- Name: schedule_types id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types ALTER COLUMN id SET DEFAULT nextval('public.schedule_types_id_seq'::regclass);


--
-- TOC entry 3321 (class 2604 OID 16611)
-- Name: transactions id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions ALTER COLUMN id SET DEFAULT nextval('public.transactions_id_seq'::regclass);


--
-- TOC entry 3306 (class 2604 OID 16451)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 3579 (class 0 OID 16620)
-- Dependencies: 250
-- Data for Name: account_interest; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.account_interest (id, account_id, rate, compound_frequency, accrual_day, interest_type, enabled, last_accrual_date) FROM stdin;
1	3	7.00	daily	\N	compound	f	\N
2	2	10.00	daily	\N	compound	f	2025-03-01
3	4	20.00	daily	15	compound	f	2025-03-01
\.


--
-- TOC entry 3549 (class 0 OID 16401)
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
-- TOC entry 3561 (class 0 OID 16464)
-- Dependencies: 232
-- Data for Name: accounts; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.accounts (id, user_id, account_name, type_id, balance) FROM stdin;
5	1	Fidelity Money Market	4	0.00
1	1	SECU Checking	1	-4396.82
3	1	Home Equity	9	340000.00
2	1	Mortgage	11	181521.26
4	1	CapitalOne Credit Card	10	1579.30
6	1	SECU Savings	2	25.71
7	1	Bank of America Van Loan	12	22726.35
\.


--
-- TOC entry 3557 (class 0 OID 16439)
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
-- TOC entry 3571 (class 0 OID 16561)
-- Dependencies: 242
-- Data for Name: expense_changes; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expense_changes (id, recurring_schedule_id, effective_date, end_date, new_amount) FROM stdin;
\.


--
-- TOC entry 3575 (class 0 OID 16590)
-- Dependencies: 246
-- Data for Name: expense_payments; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.expense_payments (id, expense_id, account_id, payment_date, amount) FROM stdin;
\.


--
-- TOC entry 3569 (class 0 OID 16537)
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
2064	1	2026-10-11	1	30.00	RAV4 State Inspection	f	83	\N	\N
2065	1	2027-10-11	1	30.00	RAV4 State Inspection	f	83	\N	\N
100	1	2026-07-30	1	80.00	Gas	f	21	\N	\N
101	1	2026-08-13	1	80.00	Gas	f	21	\N	\N
102	1	2026-08-27	1	80.00	Gas	f	21	\N	\N
37	1	2025-03-13	1	80.00	Gas	f	21	\N	7104
38	1	2025-03-27	1	80.00	Gas	f	21	\N	7105
2081	1	2025-10-31	4	100.00	New Baby Birthday	f	88	\N	\N
2790	1	2025-08-14	8	500.00	Emergency Fund	f	108	\N	\N
51	1	2025-03-13	5	400.00	Groceries	f	23	\N	7104
52	1	2025-03-27	5	400.00	Groceries	f	23	\N	7105
103	1	2026-09-10	1	80.00	Gas	f	21	\N	\N
57	1	2025-03-15	3	18.14	Apple Music	f	24	\N	7104
2777	1	2025-03-24	7	240.00	Kobo Libra Colour	f	\N		7112
43	1	2025-06-01	6	1670.20	Mortgage	f	22	\N	7109
104	1	2026-09-24	1	80.00	Gas	f	21	\N	\N
105	1	2026-10-08	1	80.00	Gas	f	21	\N	\N
59	1	2025-05-15	3	18.14	Apple Music	f	24	\N	7108
601	1	2025-05-17	7	9.99	iCloud 2TB	f	36	\N	7108
602	1	2025-06-17	7	9.99	iCloud 2TB	f	36	\N	7110
2784	1	2025-05-22	8	500.00	Emergency Fund	f	108	\N	7109
2785	1	2025-06-05	8	500.00	Emergency Fund	f	108	\N	7110
45	1	2025-08-01	6	1670.20	Mortgage	f	22	\N	\N
46	1	2025-09-01	6	1670.20	Mortgage	f	22	\N	\N
47	1	2025-10-01	6	1670.20	Mortgage	f	22	\N	\N
106	1	2026-10-22	1	80.00	Gas	f	21	\N	\N
48	1	2025-11-01	6	1670.20	Mortgage	f	22	\N	\N
49	1	2025-12-01	6	1670.20	Mortgage	f	22	\N	\N
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
44	1	2025-07-01	6	1670.20	Mortgage	f	22	\N	7111
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
2082	1	2025-11-14	4	100.00	New Baby Birthday	f	88	\N	\N
2083	1	2025-11-28	4	100.00	New Baby Birthday	f	88	\N	\N
2084	1	2025-12-12	4	100.00	New Baby Birthday	f	88	\N	\N
2805	1	2026-03-12	8	500.00	Emergency Fund	f	108	\N	\N
2296	1	2025-07-06	4	100.00	Mother's Day	f	92	\N	7111
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
2297	1	2025-07-20	4	100.00	Mother's Day	f	92	\N	7111
2069	1	2026-01-11	1	208.17	Van Property Tax	f	85	\N	\N
2070	1	2027-01-11	1	208.17	Van Property Tax	f	85	\N	\N
2085	1	2025-12-26	4	100.00	New Baby Birthday	f	88	\N	\N
2086	1	2026-01-09	4	100.00	New Baby Birthday	f	88	\N	\N
2087	1	2026-01-23	4	100.00	New Baby Birthday	f	88	\N	\N
2088	1	2026-02-06	4	100.00	New Baby Birthday	f	88	\N	\N
2089	1	2026-02-20	4	100.00	New Baby Birthday	f	88	\N	\N
2090	1	2026-03-06	4	100.00	New Baby Birthday	f	88	\N	\N
2091	1	2026-03-20	4	100.00	New Baby Birthday	f	88	\N	\N
2092	1	2026-04-03	4	100.00	New Baby Birthday	f	88	\N	\N
2093	1	2026-04-17	4	100.00	New Baby Birthday	f	88	\N	\N
2094	1	2026-05-01	4	100.00	New Baby Birthday	f	88	\N	\N
2095	1	2026-05-15	4	100.00	New Baby Birthday	f	88	\N	\N
2096	1	2026-05-29	4	100.00	New Baby Birthday	f	88	\N	\N
2097	1	2026-06-12	4	100.00	New Baby Birthday	f	88	\N	\N
2098	1	2026-06-26	4	100.00	New Baby Birthday	f	88	\N	\N
2099	1	2026-07-10	4	100.00	New Baby Birthday	f	88	\N	\N
2100	1	2026-07-24	4	100.00	New Baby Birthday	f	88	\N	\N
2101	1	2026-08-07	4	100.00	New Baby Birthday	f	88	\N	\N
2102	1	2026-08-21	4	100.00	New Baby Birthday	f	88	\N	\N
2103	1	2026-09-04	4	100.00	New Baby Birthday	f	88	\N	\N
2104	1	2026-09-18	4	100.00	New Baby Birthday	f	88	\N	\N
2105	1	2026-10-02	4	100.00	New Baby Birthday	f	88	\N	\N
2106	1	2026-10-16	4	100.00	New Baby Birthday	f	88	\N	\N
2107	1	2026-10-30	4	100.00	New Baby Birthday	f	88	\N	\N
2108	1	2026-11-13	4	100.00	New Baby Birthday	f	88	\N	\N
2109	1	2026-11-27	4	100.00	New Baby Birthday	f	88	\N	\N
2110	1	2026-12-11	4	100.00	New Baby Birthday	f	88	\N	\N
2111	1	2026-12-25	4	100.00	New Baby Birthday	f	88	\N	\N
2112	1	2027-01-08	4	100.00	New Baby Birthday	f	88	\N	\N
2113	1	2027-01-22	4	100.00	New Baby Birthday	f	88	\N	\N
2114	1	2027-02-05	4	100.00	New Baby Birthday	f	88	\N	\N
2115	1	2027-02-19	4	100.00	New Baby Birthday	f	88	\N	\N
2116	1	2027-03-05	4	100.00	New Baby Birthday	f	88	\N	\N
2117	1	2027-03-19	4	100.00	New Baby Birthday	f	88	\N	\N
2118	1	2027-04-02	4	100.00	New Baby Birthday	f	88	\N	\N
2119	1	2027-04-16	4	100.00	New Baby Birthday	f	88	\N	\N
2120	1	2027-04-30	4	100.00	New Baby Birthday	f	88	\N	\N
2121	1	2027-05-14	4	100.00	New Baby Birthday	f	88	\N	\N
2122	1	2027-05-28	4	100.00	New Baby Birthday	f	88	\N	\N
2123	1	2027-06-11	4	100.00	New Baby Birthday	f	88	\N	\N
2124	1	2027-06-25	4	100.00	New Baby Birthday	f	88	\N	\N
2125	1	2027-07-09	4	100.00	New Baby Birthday	f	88	\N	\N
2126	1	2027-07-23	4	100.00	New Baby Birthday	f	88	\N	\N
2127	1	2027-08-06	4	100.00	New Baby Birthday	f	88	\N	\N
2128	1	2027-08-20	4	100.00	New Baby Birthday	f	88	\N	\N
2806	1	2026-03-26	8	500.00	Emergency Fund	f	108	\N	\N
2130	1	2025-10-21	4	100.00	Ariella's Birthday	f	89	\N	\N
2131	1	2025-11-04	4	100.00	Ariella's Birthday	f	89	\N	\N
2132	1	2025-11-18	4	100.00	Ariella's Birthday	f	89	\N	\N
2133	1	2025-12-02	4	100.00	Ariella's Birthday	f	89	\N	\N
2134	1	2025-12-16	4	100.00	Ariella's Birthday	f	89	\N	\N
2135	1	2025-12-30	4	100.00	Ariella's Birthday	f	89	\N	\N
2136	1	2026-01-13	4	100.00	Ariella's Birthday	f	89	\N	\N
2807	1	2026-04-09	8	500.00	Emergency Fund	f	108	\N	\N
313	1	2026-04-11	1	175.00	Oil & Air Filters	f	28	\N	\N
314	1	2026-10-11	1	175.00	Oil & Air Filters	f	28	\N	\N
315	1	2027-04-11	1	175.00	Oil & Air Filters	f	28	\N	\N
2137	1	2026-01-27	4	100.00	Ariella's Birthday	f	89	\N	\N
2138	1	2026-02-10	4	100.00	Ariella's Birthday	f	89	\N	\N
2139	1	2026-02-24	4	100.00	Ariella's Birthday	f	89	\N	\N
2140	1	2026-03-10	4	100.00	Ariella's Birthday	f	89	\N	\N
2141	1	2026-03-24	4	100.00	Ariella's Birthday	f	89	\N	\N
2142	1	2026-04-07	4	100.00	Ariella's Birthday	f	89	\N	\N
2143	1	2026-04-21	4	100.00	Ariella's Birthday	f	89	\N	\N
2144	1	2026-05-05	4	100.00	Ariella's Birthday	f	89	\N	\N
2145	1	2026-05-19	4	100.00	Ariella's Birthday	f	89	\N	\N
2146	1	2026-06-02	4	100.00	Ariella's Birthday	f	89	\N	\N
2147	1	2026-06-16	4	100.00	Ariella's Birthday	f	89	\N	\N
2148	1	2026-06-30	4	100.00	Ariella's Birthday	f	89	\N	\N
2149	1	2026-07-14	4	100.00	Ariella's Birthday	f	89	\N	\N
2150	1	2026-07-28	4	100.00	Ariella's Birthday	f	89	\N	\N
2151	1	2026-08-11	4	100.00	Ariella's Birthday	f	89	\N	\N
2152	1	2026-08-25	4	100.00	Ariella's Birthday	f	89	\N	\N
2153	1	2026-09-08	4	100.00	Ariella's Birthday	f	89	\N	\N
2154	1	2026-09-22	4	100.00	Ariella's Birthday	f	89	\N	\N
2155	1	2026-10-06	4	100.00	Ariella's Birthday	f	89	\N	\N
2156	1	2026-10-20	4	100.00	Ariella's Birthday	f	89	\N	\N
2157	1	2026-11-03	4	100.00	Ariella's Birthday	f	89	\N	\N
2158	1	2026-11-17	4	100.00	Ariella's Birthday	f	89	\N	\N
2159	1	2026-12-01	4	100.00	Ariella's Birthday	f	89	\N	\N
2160	1	2026-12-15	4	100.00	Ariella's Birthday	f	89	\N	\N
2161	1	2026-12-29	4	100.00	Ariella's Birthday	f	89	\N	\N
2162	1	2027-01-12	4	100.00	Ariella's Birthday	f	89	\N	\N
2163	1	2027-01-26	4	100.00	Ariella's Birthday	f	89	\N	\N
2164	1	2027-02-09	4	100.00	Ariella's Birthday	f	89	\N	\N
2165	1	2027-02-23	4	100.00	Ariella's Birthday	f	89	\N	\N
2166	1	2027-03-09	4	100.00	Ariella's Birthday	f	89	\N	\N
2167	1	2027-03-23	4	100.00	Ariella's Birthday	f	89	\N	\N
2071	1	2026-01-08	4	100.00	Eliana's Birthday	f	86	\N	\N
2072	1	2027-01-08	4	100.00	Eliana's Birthday	f	86	\N	\N
2168	1	2027-04-06	4	100.00	Ariella's Birthday	f	89	\N	\N
2169	1	2027-04-20	4	100.00	Ariella's Birthday	f	89	\N	\N
2170	1	2027-05-04	4	100.00	Ariella's Birthday	f	89	\N	\N
2171	1	2027-05-18	4	100.00	Ariella's Birthday	f	89	\N	\N
2172	1	2027-06-01	4	100.00	Ariella's Birthday	f	89	\N	\N
2173	1	2027-06-15	4	100.00	Ariella's Birthday	f	89	\N	\N
2174	1	2027-06-29	4	100.00	Ariella's Birthday	f	89	\N	\N
2175	1	2027-07-13	4	100.00	Ariella's Birthday	f	89	\N	\N
2176	1	2027-07-27	4	100.00	Ariella's Birthday	f	89	\N	\N
2177	1	2027-08-10	4	100.00	Ariella's Birthday	f	89	\N	\N
2178	1	2027-08-24	4	100.00	Ariella's Birthday	f	89	\N	\N
2179	1	2027-09-07	4	100.00	Ariella's Birthday	f	89	\N	\N
2180	1	2027-09-21	4	100.00	Ariella's Birthday	f	89	\N	\N
2181	1	2027-10-05	4	100.00	Ariella's Birthday	f	89	\N	\N
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
42	1	2025-05-01	6	1670.20	Mortgage	f	22	\N	7107
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
2348	1	2025-07-09	4	100.00	Father's Day	f	93	\N	7111
2349	1	2025-07-23	4	100.00	Father's Day	f	93	\N	7111
2837	1	2025-05-19	4	100.00	Josh's Birthday	f	114	\N	7108
2074	1	2026-05-01	4	100.00	Kayla's Birthday	f	87	\N	\N
2075	1	2027-05-01	4	100.00	Kayla's Birthday	f	87	\N	\N
2182	1	2025-11-01	4	400.00	Christmas	f	90	\N	\N
2183	1	2025-11-15	4	400.00	Christmas	f	90	\N	\N
2184	1	2025-11-29	4	400.00	Christmas	f	90	\N	\N
2185	1	2025-12-13	4	400.00	Christmas	f	90	\N	\N
2186	1	2025-12-27	4	400.00	Christmas	f	90	\N	\N
2187	1	2026-01-10	4	400.00	Christmas	f	90	\N	\N
2188	1	2026-01-24	4	400.00	Christmas	f	90	\N	\N
2189	1	2026-02-07	4	400.00	Christmas	f	90	\N	\N
2190	1	2026-02-21	4	400.00	Christmas	f	90	\N	\N
2191	1	2026-03-07	4	400.00	Christmas	f	90	\N	\N
2192	1	2026-03-21	4	400.00	Christmas	f	90	\N	\N
2193	1	2026-04-04	4	400.00	Christmas	f	90	\N	\N
2194	1	2026-04-18	4	400.00	Christmas	f	90	\N	\N
2195	1	2026-05-02	4	400.00	Christmas	f	90	\N	\N
2196	1	2026-05-16	4	400.00	Christmas	f	90	\N	\N
2197	1	2026-05-30	4	400.00	Christmas	f	90	\N	\N
2198	1	2026-06-13	4	400.00	Christmas	f	90	\N	\N
2199	1	2026-06-27	4	400.00	Christmas	f	90	\N	\N
2200	1	2026-07-11	4	400.00	Christmas	f	90	\N	\N
2201	1	2026-07-25	4	400.00	Christmas	f	90	\N	\N
2202	1	2026-08-08	4	400.00	Christmas	f	90	\N	\N
2203	1	2026-08-22	4	400.00	Christmas	f	90	\N	\N
2204	1	2026-09-05	4	400.00	Christmas	f	90	\N	\N
2205	1	2026-09-19	4	400.00	Christmas	f	90	\N	\N
2206	1	2026-10-03	4	400.00	Christmas	f	90	\N	\N
2207	1	2026-10-17	4	400.00	Christmas	f	90	\N	\N
2208	1	2026-10-31	4	400.00	Christmas	f	90	\N	\N
2209	1	2026-11-14	4	400.00	Christmas	f	90	\N	\N
2210	1	2026-11-28	4	400.00	Christmas	f	90	\N	\N
2211	1	2026-12-12	4	400.00	Christmas	f	90	\N	\N
2212	1	2026-12-26	4	400.00	Christmas	f	90	\N	\N
2213	1	2027-01-09	4	400.00	Christmas	f	90	\N	\N
2214	1	2027-01-23	4	400.00	Christmas	f	90	\N	\N
2215	1	2027-02-06	4	400.00	Christmas	f	90	\N	\N
2216	1	2027-02-20	4	400.00	Christmas	f	90	\N	\N
2217	1	2027-03-06	4	400.00	Christmas	f	90	\N	\N
2218	1	2027-03-20	4	400.00	Christmas	f	90	\N	\N
2219	1	2027-04-03	4	400.00	Christmas	f	90	\N	\N
2220	1	2027-04-17	4	400.00	Christmas	f	90	\N	\N
2221	1	2027-05-01	4	400.00	Christmas	f	90	\N	\N
2222	1	2027-05-15	4	400.00	Christmas	f	90	\N	\N
2223	1	2027-05-29	4	400.00	Christmas	f	90	\N	\N
2224	1	2027-06-12	4	400.00	Christmas	f	90	\N	\N
2225	1	2027-06-26	4	400.00	Christmas	f	90	\N	\N
2226	1	2027-07-10	4	400.00	Christmas	f	90	\N	\N
2227	1	2027-07-24	4	400.00	Christmas	f	90	\N	\N
2228	1	2027-08-07	4	400.00	Christmas	f	90	\N	\N
2229	1	2027-08-21	4	400.00	Christmas	f	90	\N	\N
2230	1	2027-09-04	4	400.00	Christmas	f	90	\N	\N
2231	1	2027-09-18	4	400.00	Christmas	f	90	\N	\N
2232	1	2027-10-02	4	400.00	Christmas	f	90	\N	\N
2233	1	2027-10-16	4	400.00	Christmas	f	90	\N	\N
2234	1	2027-10-30	4	400.00	Christmas	f	90	\N	\N
2235	1	2027-11-13	4	400.00	Christmas	f	90	\N	\N
2236	1	2027-11-27	4	400.00	Christmas	f	90	\N	\N
2838	1	2025-06-02	4	100.00	Josh's Birthday	f	114	\N	7109
2839	1	2025-06-16	4	100.00	Josh's Birthday	f	114	\N	7110
2840	1	2025-06-30	4	100.00	Josh's Birthday	f	114	\N	7111
553	1	2025-07-26	3	15.96	Audible	f	34	\N	7111
2245	1	2025-10-21	4	600.00	School Curriculum	f	91	\N	\N
2246	1	2025-11-04	4	600.00	School Curriculum	f	91	\N	\N
2247	1	2025-11-18	4	600.00	School Curriculum	f	91	\N	\N
2248	1	2025-12-02	4	600.00	School Curriculum	f	91	\N	\N
2249	1	2025-12-16	4	600.00	School Curriculum	f	91	\N	\N
2250	1	2025-12-30	4	600.00	School Curriculum	f	91	\N	\N
2251	1	2026-01-13	4	600.00	School Curriculum	f	91	\N	\N
2252	1	2026-01-27	4	600.00	School Curriculum	f	91	\N	\N
2253	1	2026-02-10	4	600.00	School Curriculum	f	91	\N	\N
2843	1	2025-08-11	4	100.00	Josh's Birthday	f	114	\N	\N
2844	1	2025-08-25	4	100.00	Josh's Birthday	f	114	\N	\N
2845	1	2025-09-08	4	100.00	Josh's Birthday	f	114	\N	\N
2841	1	2025-07-14	4	100.00	Josh's Birthday	f	114	\N	7111
2842	1	2025-07-28	4	100.00	Josh's Birthday	f	114	\N	7111
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
2254	1	2026-02-24	4	600.00	School Curriculum	f	91	\N	\N
2255	1	2026-03-10	4	600.00	School Curriculum	f	91	\N	\N
2256	1	2026-03-24	4	600.00	School Curriculum	f	91	\N	\N
2257	1	2026-04-07	4	600.00	School Curriculum	f	91	\N	\N
2846	1	2025-09-22	4	100.00	Josh's Birthday	f	114	\N	\N
2847	1	2025-10-06	4	100.00	Josh's Birthday	f	114	\N	\N
2848	1	2025-10-20	4	100.00	Josh's Birthday	f	114	\N	\N
2849	1	2025-11-03	4	100.00	Josh's Birthday	f	114	\N	\N
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
2258	1	2026-04-21	4	600.00	School Curriculum	f	91	\N	\N
2259	1	2026-05-05	4	600.00	School Curriculum	f	91	\N	\N
2260	1	2026-05-19	4	600.00	School Curriculum	f	91	\N	\N
2261	1	2026-06-02	4	600.00	School Curriculum	f	91	\N	\N
2262	1	2026-06-16	4	600.00	School Curriculum	f	91	\N	\N
2263	1	2026-06-30	4	600.00	School Curriculum	f	91	\N	\N
2264	1	2026-07-14	4	600.00	School Curriculum	f	91	\N	\N
2265	1	2026-07-28	4	600.00	School Curriculum	f	91	\N	\N
2266	1	2026-08-11	4	600.00	School Curriculum	f	91	\N	\N
2267	1	2026-08-25	4	600.00	School Curriculum	f	91	\N	\N
2268	1	2026-09-08	4	600.00	School Curriculum	f	91	\N	\N
2269	1	2026-09-22	4	600.00	School Curriculum	f	91	\N	\N
2270	1	2026-10-06	4	600.00	School Curriculum	f	91	\N	\N
2271	1	2026-10-20	4	600.00	School Curriculum	f	91	\N	\N
2272	1	2026-11-03	4	600.00	School Curriculum	f	91	\N	\N
2273	1	2026-11-17	4	600.00	School Curriculum	f	91	\N	\N
2274	1	2026-12-01	4	600.00	School Curriculum	f	91	\N	\N
2275	1	2026-12-15	4	600.00	School Curriculum	f	91	\N	\N
2276	1	2026-12-29	4	600.00	School Curriculum	f	91	\N	\N
2277	1	2027-01-12	4	600.00	School Curriculum	f	91	\N	\N
2278	1	2027-01-26	4	600.00	School Curriculum	f	91	\N	\N
2279	1	2027-02-09	4	600.00	School Curriculum	f	91	\N	\N
2280	1	2027-02-23	4	600.00	School Curriculum	f	91	\N	\N
2281	1	2027-03-09	4	600.00	School Curriculum	f	91	\N	\N
2282	1	2027-03-23	4	600.00	School Curriculum	f	91	\N	\N
2283	1	2027-04-06	4	600.00	School Curriculum	f	91	\N	\N
2284	1	2027-04-20	4	600.00	School Curriculum	f	91	\N	\N
2285	1	2027-05-04	4	600.00	School Curriculum	f	91	\N	\N
2286	1	2027-05-18	4	600.00	School Curriculum	f	91	\N	\N
2287	1	2027-06-01	4	600.00	School Curriculum	f	91	\N	\N
2288	1	2027-06-15	4	600.00	School Curriculum	f	91	\N	\N
2289	1	2027-06-29	4	600.00	School Curriculum	f	91	\N	\N
2290	1	2027-07-13	4	600.00	School Curriculum	f	91	\N	\N
2291	1	2027-07-27	4	600.00	School Curriculum	f	91	\N	\N
2850	1	2025-11-17	4	100.00	Josh's Birthday	f	114	\N	\N
2851	1	2025-12-01	4	100.00	Josh's Birthday	f	114	\N	\N
2852	1	2025-12-15	4	100.00	Josh's Birthday	f	114	\N	\N
2853	1	2025-12-29	4	100.00	Josh's Birthday	f	114	\N	\N
2854	1	2026-01-12	4	100.00	Josh's Birthday	f	114	\N	\N
2855	1	2026-01-26	4	100.00	Josh's Birthday	f	114	\N	\N
2856	1	2026-02-09	4	100.00	Josh's Birthday	f	114	\N	\N
2857	1	2026-02-23	4	100.00	Josh's Birthday	f	114	\N	\N
2858	1	2026-03-09	4	100.00	Josh's Birthday	f	114	\N	\N
2859	1	2026-03-23	4	100.00	Josh's Birthday	f	114	\N	\N
2304	1	2025-10-26	4	100.00	Mother's Day	f	92	\N	\N
2305	1	2025-11-09	4	100.00	Mother's Day	f	92	\N	\N
2306	1	2025-11-23	4	100.00	Mother's Day	f	92	\N	\N
2307	1	2025-12-07	4	100.00	Mother's Day	f	92	\N	\N
2308	1	2025-12-21	4	100.00	Mother's Day	f	92	\N	\N
2309	1	2026-01-04	4	100.00	Mother's Day	f	92	\N	\N
2310	1	2026-01-18	4	100.00	Mother's Day	f	92	\N	\N
2311	1	2026-02-01	4	100.00	Mother's Day	f	92	\N	\N
2312	1	2026-02-15	4	100.00	Mother's Day	f	92	\N	\N
2313	1	2026-03-01	4	100.00	Mother's Day	f	92	\N	\N
2314	1	2026-03-15	4	100.00	Mother's Day	f	92	\N	\N
2315	1	2026-03-29	4	100.00	Mother's Day	f	92	\N	\N
2316	1	2026-04-12	4	100.00	Mother's Day	f	92	\N	\N
2317	1	2026-04-26	4	100.00	Mother's Day	f	92	\N	\N
2318	1	2026-05-10	4	100.00	Mother's Day	f	92	\N	\N
2319	1	2026-05-24	4	100.00	Mother's Day	f	92	\N	\N
2320	1	2026-06-07	4	100.00	Mother's Day	f	92	\N	\N
2321	1	2026-06-21	4	100.00	Mother's Day	f	92	\N	\N
2322	1	2026-07-05	4	100.00	Mother's Day	f	92	\N	\N
2323	1	2026-07-19	4	100.00	Mother's Day	f	92	\N	\N
2324	1	2026-08-02	4	100.00	Mother's Day	f	92	\N	\N
2325	1	2026-08-16	4	100.00	Mother's Day	f	92	\N	\N
2326	1	2026-08-30	4	100.00	Mother's Day	f	92	\N	\N
2327	1	2026-09-13	4	100.00	Mother's Day	f	92	\N	\N
2328	1	2026-09-27	4	100.00	Mother's Day	f	92	\N	\N
2329	1	2026-10-11	4	100.00	Mother's Day	f	92	\N	\N
2330	1	2026-10-25	4	100.00	Mother's Day	f	92	\N	\N
2331	1	2026-11-08	4	100.00	Mother's Day	f	92	\N	\N
2332	1	2026-11-22	4	100.00	Mother's Day	f	92	\N	\N
2333	1	2026-12-06	4	100.00	Mother's Day	f	92	\N	\N
2334	1	2026-12-20	4	100.00	Mother's Day	f	92	\N	\N
2335	1	2027-01-03	4	100.00	Mother's Day	f	92	\N	\N
2336	1	2027-01-17	4	100.00	Mother's Day	f	92	\N	\N
2337	1	2027-01-31	4	100.00	Mother's Day	f	92	\N	\N
2338	1	2027-02-14	4	100.00	Mother's Day	f	92	\N	\N
2339	1	2027-02-28	4	100.00	Mother's Day	f	92	\N	\N
2340	1	2027-03-14	4	100.00	Mother's Day	f	92	\N	\N
2341	1	2027-03-28	4	100.00	Mother's Day	f	92	\N	\N
2342	1	2027-04-11	4	100.00	Mother's Day	f	92	\N	\N
2343	1	2027-04-25	4	100.00	Mother's Day	f	92	\N	\N
2344	1	2027-05-09	4	100.00	Mother's Day	f	92	\N	\N
2345	1	2027-05-23	4	100.00	Mother's Day	f	92	\N	\N
2860	1	2026-04-06	4	100.00	Josh's Birthday	f	114	\N	\N
2861	1	2026-04-20	4	100.00	Josh's Birthday	f	114	\N	\N
2862	1	2026-05-04	4	100.00	Josh's Birthday	f	114	\N	\N
2863	1	2026-05-18	4	100.00	Josh's Birthday	f	114	\N	\N
2864	1	2026-06-01	4	100.00	Josh's Birthday	f	114	\N	\N
2865	1	2026-06-15	4	100.00	Josh's Birthday	f	114	\N	\N
2866	1	2026-06-29	4	100.00	Josh's Birthday	f	114	\N	\N
2867	1	2026-07-13	4	100.00	Josh's Birthday	f	114	\N	\N
2868	1	2026-07-27	4	100.00	Josh's Birthday	f	114	\N	\N
2869	1	2026-08-10	4	100.00	Josh's Birthday	f	114	\N	\N
2870	1	2026-08-24	4	100.00	Josh's Birthday	f	114	\N	\N
2871	1	2026-09-07	4	100.00	Josh's Birthday	f	114	\N	\N
2872	1	2026-09-21	4	100.00	Josh's Birthday	f	114	\N	\N
2873	1	2026-10-05	4	100.00	Josh's Birthday	f	114	\N	\N
2874	1	2026-10-19	4	100.00	Josh's Birthday	f	114	\N	\N
2875	1	2026-11-02	4	100.00	Josh's Birthday	f	114	\N	\N
2876	1	2026-11-16	4	100.00	Josh's Birthday	f	114	\N	\N
2877	1	2026-11-30	4	100.00	Josh's Birthday	f	114	\N	\N
2878	1	2026-12-14	4	100.00	Josh's Birthday	f	114	\N	\N
2879	1	2026-12-28	4	100.00	Josh's Birthday	f	114	\N	\N
2880	1	2027-01-11	4	100.00	Josh's Birthday	f	114	\N	\N
2881	1	2027-01-25	4	100.00	Josh's Birthday	f	114	\N	\N
2882	1	2027-02-08	4	100.00	Josh's Birthday	f	114	\N	\N
2883	1	2027-02-22	4	100.00	Josh's Birthday	f	114	\N	\N
2884	1	2027-03-08	4	100.00	Josh's Birthday	f	114	\N	\N
2885	1	2027-03-22	4	100.00	Josh's Birthday	f	114	\N	\N
68	1	2025-05-08	1	80.00	Gas	f	21	\N	7108
2833	1	2025-03-24	4	100.00	Josh's Birthday	f	114	\N	7104
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
2356	1	2025-10-29	4	100.00	Father's Day	f	93	\N	\N
2357	1	2025-11-12	4	100.00	Father's Day	f	93	\N	\N
2358	1	2025-11-26	4	100.00	Father's Day	f	93	\N	\N
2359	1	2025-12-10	4	100.00	Father's Day	f	93	\N	\N
2360	1	2025-12-24	4	100.00	Father's Day	f	93	\N	\N
2361	1	2026-01-07	4	100.00	Father's Day	f	93	\N	\N
2362	1	2026-01-21	4	100.00	Father's Day	f	93	\N	\N
2363	1	2026-02-04	4	100.00	Father's Day	f	93	\N	\N
2364	1	2026-02-18	4	100.00	Father's Day	f	93	\N	\N
2365	1	2026-03-04	4	100.00	Father's Day	f	93	\N	\N
2366	1	2026-03-18	4	100.00	Father's Day	f	93	\N	\N
2367	1	2026-04-01	4	100.00	Father's Day	f	93	\N	\N
2368	1	2026-04-15	4	100.00	Father's Day	f	93	\N	\N
2369	1	2026-04-29	4	100.00	Father's Day	f	93	\N	\N
2370	1	2026-05-13	4	100.00	Father's Day	f	93	\N	\N
2371	1	2026-05-27	4	100.00	Father's Day	f	93	\N	\N
2372	1	2026-06-10	4	100.00	Father's Day	f	93	\N	\N
2373	1	2026-06-24	4	100.00	Father's Day	f	93	\N	\N
2374	1	2026-07-08	4	100.00	Father's Day	f	93	\N	\N
2375	1	2026-07-22	4	100.00	Father's Day	f	93	\N	\N
2376	1	2026-08-05	4	100.00	Father's Day	f	93	\N	\N
2377	1	2026-08-19	4	100.00	Father's Day	f	93	\N	\N
2378	1	2026-09-02	4	100.00	Father's Day	f	93	\N	\N
2379	1	2026-09-16	4	100.00	Father's Day	f	93	\N	\N
2380	1	2026-09-30	4	100.00	Father's Day	f	93	\N	\N
2381	1	2026-10-14	4	100.00	Father's Day	f	93	\N	\N
2382	1	2026-10-28	4	100.00	Father's Day	f	93	\N	\N
2383	1	2026-11-11	4	100.00	Father's Day	f	93	\N	\N
2384	1	2026-11-25	4	100.00	Father's Day	f	93	\N	\N
2385	1	2026-12-09	4	100.00	Father's Day	f	93	\N	\N
2386	1	2026-12-23	4	100.00	Father's Day	f	93	\N	\N
2387	1	2027-01-06	4	100.00	Father's Day	f	93	\N	\N
2388	1	2027-01-20	4	100.00	Father's Day	f	93	\N	\N
2389	1	2027-02-03	4	100.00	Father's Day	f	93	\N	\N
2390	1	2027-02-17	4	100.00	Father's Day	f	93	\N	\N
2391	1	2027-03-03	4	100.00	Father's Day	f	93	\N	\N
2392	1	2027-03-17	4	100.00	Father's Day	f	93	\N	\N
2393	1	2027-03-31	4	100.00	Father's Day	f	93	\N	\N
2394	1	2027-04-14	4	100.00	Father's Day	f	93	\N	\N
2395	1	2027-04-28	4	100.00	Father's Day	f	93	\N	\N
2396	1	2027-05-12	4	100.00	Father's Day	f	93	\N	\N
2397	1	2027-05-26	4	100.00	Father's Day	f	93	\N	\N
2398	1	2027-06-09	4	100.00	Father's Day	f	93	\N	\N
2399	1	2027-06-23	4	100.00	Father's Day	f	93	\N	\N
2401	1	2025-11-01	4	100.00	Wedding Anniversary	f	94	\N	\N
2402	1	2025-11-15	4	100.00	Wedding Anniversary	f	94	\N	\N
2403	1	2025-11-29	4	100.00	Wedding Anniversary	f	94	\N	\N
2404	1	2025-12-13	4	100.00	Wedding Anniversary	f	94	\N	\N
2405	1	2025-12-27	4	100.00	Wedding Anniversary	f	94	\N	\N
2406	1	2026-01-10	4	100.00	Wedding Anniversary	f	94	\N	\N
2407	1	2026-01-24	4	100.00	Wedding Anniversary	f	94	\N	\N
2408	1	2026-02-07	4	100.00	Wedding Anniversary	f	94	\N	\N
2409	1	2026-02-21	4	100.00	Wedding Anniversary	f	94	\N	\N
2410	1	2026-03-07	4	100.00	Wedding Anniversary	f	94	\N	\N
2411	1	2026-03-21	4	100.00	Wedding Anniversary	f	94	\N	\N
2412	1	2026-04-04	4	100.00	Wedding Anniversary	f	94	\N	\N
2413	1	2026-04-18	4	100.00	Wedding Anniversary	f	94	\N	\N
2414	1	2026-05-02	4	100.00	Wedding Anniversary	f	94	\N	\N
2415	1	2026-05-16	4	100.00	Wedding Anniversary	f	94	\N	\N
2416	1	2026-05-30	4	100.00	Wedding Anniversary	f	94	\N	\N
2417	1	2026-06-13	4	100.00	Wedding Anniversary	f	94	\N	\N
2418	1	2026-06-27	4	100.00	Wedding Anniversary	f	94	\N	\N
2419	1	2026-07-11	4	100.00	Wedding Anniversary	f	94	\N	\N
2420	1	2026-07-25	4	100.00	Wedding Anniversary	f	94	\N	\N
2421	1	2026-08-08	4	100.00	Wedding Anniversary	f	94	\N	\N
2422	1	2026-08-22	4	100.00	Wedding Anniversary	f	94	\N	\N
2423	1	2026-09-05	4	100.00	Wedding Anniversary	f	94	\N	\N
2424	1	2026-09-19	4	100.00	Wedding Anniversary	f	94	\N	\N
2425	1	2026-10-03	4	100.00	Wedding Anniversary	f	94	\N	\N
2426	1	2026-10-17	4	100.00	Wedding Anniversary	f	94	\N	\N
2427	1	2026-10-31	4	100.00	Wedding Anniversary	f	94	\N	\N
2428	1	2026-11-14	4	100.00	Wedding Anniversary	f	94	\N	\N
2429	1	2026-11-28	4	100.00	Wedding Anniversary	f	94	\N	\N
2430	1	2026-12-12	4	100.00	Wedding Anniversary	f	94	\N	\N
2431	1	2026-12-26	4	100.00	Wedding Anniversary	f	94	\N	\N
2432	1	2027-01-09	4	100.00	Wedding Anniversary	f	94	\N	\N
2433	1	2027-01-23	4	100.00	Wedding Anniversary	f	94	\N	\N
2434	1	2027-02-06	4	100.00	Wedding Anniversary	f	94	\N	\N
2435	1	2027-02-20	4	100.00	Wedding Anniversary	f	94	\N	\N
2436	1	2027-03-06	4	100.00	Wedding Anniversary	f	94	\N	\N
2437	1	2027-03-20	4	100.00	Wedding Anniversary	f	94	\N	\N
2438	1	2027-04-03	4	100.00	Wedding Anniversary	f	94	\N	\N
2439	1	2027-04-17	4	100.00	Wedding Anniversary	f	94	\N	\N
2440	1	2027-05-01	4	100.00	Wedding Anniversary	f	94	\N	\N
2441	1	2027-05-15	4	100.00	Wedding Anniversary	f	94	\N	\N
2442	1	2027-05-29	4	100.00	Wedding Anniversary	f	94	\N	\N
2443	1	2027-06-12	4	100.00	Wedding Anniversary	f	94	\N	\N
2444	1	2027-06-26	4	100.00	Wedding Anniversary	f	94	\N	\N
2445	1	2027-07-10	4	100.00	Wedding Anniversary	f	94	\N	\N
2446	1	2027-07-24	4	100.00	Wedding Anniversary	f	94	\N	\N
2447	1	2027-08-07	4	100.00	Wedding Anniversary	f	94	\N	\N
2448	1	2027-08-21	4	100.00	Wedding Anniversary	f	94	\N	\N
2449	1	2027-09-04	4	100.00	Wedding Anniversary	f	94	\N	\N
2450	1	2027-09-18	4	100.00	Wedding Anniversary	f	94	\N	\N
2451	1	2027-10-02	4	100.00	Wedding Anniversary	f	94	\N	\N
2452	1	2027-10-16	4	100.00	Wedding Anniversary	f	94	\N	\N
2453	1	2027-10-30	4	100.00	Wedding Anniversary	f	94	\N	\N
2454	1	2025-11-11	7	1300.00	Josh's New Phone	f	95	\N	\N
2455	1	2025-12-09	7	1300.00	Josh's New Phone	f	95	\N	\N
2456	1	2026-01-06	7	1300.00	Josh's New Phone	f	95	\N	\N
2457	1	2026-02-03	7	1300.00	Josh's New Phone	f	95	\N	\N
2458	1	2026-03-03	7	1300.00	Josh's New Phone	f	95	\N	\N
2459	1	2026-03-31	7	1300.00	Josh's New Phone	f	95	\N	\N
2460	1	2026-04-28	7	1300.00	Josh's New Phone	f	95	\N	\N
2461	1	2026-05-26	7	1300.00	Josh's New Phone	f	95	\N	\N
2462	1	2026-06-23	7	1300.00	Josh's New Phone	f	95	\N	\N
2463	1	2026-07-21	7	1300.00	Josh's New Phone	f	95	\N	\N
2464	1	2026-08-18	7	1300.00	Josh's New Phone	f	95	\N	\N
2465	1	2026-09-15	7	1300.00	Josh's New Phone	f	95	\N	\N
2466	1	2026-10-13	7	1300.00	Josh's New Phone	f	95	\N	\N
2467	1	2026-11-10	7	1300.00	Josh's New Phone	f	95	\N	\N
2468	1	2026-12-08	7	1300.00	Josh's New Phone	f	95	\N	\N
2469	1	2027-01-05	7	1300.00	Josh's New Phone	f	95	\N	\N
2470	1	2027-02-02	7	1300.00	Josh's New Phone	f	95	\N	\N
2471	1	2027-03-02	7	1300.00	Josh's New Phone	f	95	\N	\N
2472	1	2027-03-30	7	1300.00	Josh's New Phone	f	95	\N	\N
2473	1	2027-04-27	7	1300.00	Josh's New Phone	f	95	\N	\N
2474	1	2027-05-25	7	1300.00	Josh's New Phone	f	95	\N	\N
2475	1	2027-06-22	7	1300.00	Josh's New Phone	f	95	\N	\N
2476	1	2027-07-20	7	1300.00	Josh's New Phone	f	95	\N	\N
2477	1	2027-08-17	7	1300.00	Josh's New Phone	f	95	\N	\N
2478	1	2027-09-14	7	1300.00	Josh's New Phone	f	95	\N	\N
2479	1	2027-10-12	7	1300.00	Josh's New Phone	f	95	\N	\N
2480	1	2027-11-09	7	1300.00	Josh's New Phone	f	95	\N	\N
2487	1	2025-11-03	7	287.88	Proton Family	f	96	\N	\N
2488	1	2025-11-17	7	287.88	Proton Family	f	96	\N	\N
2489	1	2025-12-01	7	287.88	Proton Family	f	96	\N	\N
2490	1	2025-12-15	7	287.88	Proton Family	f	96	\N	\N
2491	1	2025-12-29	7	287.88	Proton Family	f	96	\N	\N
2492	1	2026-01-12	7	287.88	Proton Family	f	96	\N	\N
2493	1	2026-01-26	7	287.88	Proton Family	f	96	\N	\N
2494	1	2026-02-09	7	287.88	Proton Family	f	96	\N	\N
2495	1	2026-02-23	7	287.88	Proton Family	f	96	\N	\N
2496	1	2026-03-09	7	287.88	Proton Family	f	96	\N	\N
2497	1	2026-03-23	7	287.88	Proton Family	f	96	\N	\N
2498	1	2026-04-06	7	287.88	Proton Family	f	96	\N	\N
2499	1	2026-04-20	7	287.88	Proton Family	f	96	\N	\N
2500	1	2026-05-04	7	287.88	Proton Family	f	96	\N	\N
2501	1	2026-05-18	7	287.88	Proton Family	f	96	\N	\N
2502	1	2026-06-01	7	287.88	Proton Family	f	96	\N	\N
2503	1	2026-06-15	7	287.88	Proton Family	f	96	\N	\N
2504	1	2026-06-29	7	287.88	Proton Family	f	96	\N	\N
2505	1	2026-07-13	7	287.88	Proton Family	f	96	\N	\N
2506	1	2026-07-27	7	287.88	Proton Family	f	96	\N	\N
2507	1	2026-08-10	7	287.88	Proton Family	f	96	\N	\N
2508	1	2026-08-24	7	287.88	Proton Family	f	96	\N	\N
2509	1	2026-09-07	7	287.88	Proton Family	f	96	\N	\N
2510	1	2026-09-21	7	287.88	Proton Family	f	96	\N	\N
2511	1	2026-10-05	7	287.88	Proton Family	f	96	\N	\N
2512	1	2026-10-19	7	287.88	Proton Family	f	96	\N	\N
2513	1	2026-11-02	7	287.88	Proton Family	f	96	\N	\N
2514	1	2026-11-16	7	287.88	Proton Family	f	96	\N	\N
2515	1	2026-11-30	7	287.88	Proton Family	f	96	\N	\N
2516	1	2026-12-14	7	287.88	Proton Family	f	96	\N	\N
2517	1	2026-12-28	7	287.88	Proton Family	f	96	\N	\N
2518	1	2027-01-11	7	287.88	Proton Family	f	96	\N	\N
2519	1	2027-01-25	7	287.88	Proton Family	f	96	\N	\N
2520	1	2027-02-08	7	287.88	Proton Family	f	96	\N	\N
2521	1	2027-02-22	7	287.88	Proton Family	f	96	\N	\N
2522	1	2027-03-08	7	287.88	Proton Family	f	96	\N	\N
2523	1	2027-03-22	7	287.88	Proton Family	f	96	\N	\N
2524	1	2027-04-05	7	287.88	Proton Family	f	96	\N	\N
2525	1	2027-04-19	7	287.88	Proton Family	f	96	\N	\N
2526	1	2027-05-03	7	287.88	Proton Family	f	96	\N	\N
2527	1	2027-05-17	7	287.88	Proton Family	f	96	\N	\N
2528	1	2027-05-31	7	287.88	Proton Family	f	96	\N	\N
2529	1	2027-06-14	7	287.88	Proton Family	f	96	\N	\N
2530	1	2027-06-28	7	287.88	Proton Family	f	96	\N	\N
2531	1	2027-07-12	7	287.88	Proton Family	f	96	\N	\N
2532	1	2027-07-26	7	287.88	Proton Family	f	96	\N	\N
2533	1	2027-08-09	7	287.88	Proton Family	f	96	\N	\N
2481	1	2025-08-11	7	287.88	Proton Family	f	96	\N	\N
2482	1	2025-08-25	7	287.88	Proton Family	f	96	\N	\N
2483	1	2025-09-08	7	287.88	Proton Family	f	96	\N	\N
2484	1	2025-09-22	7	287.88	Proton Family	f	96	\N	\N
2485	1	2025-10-06	7	287.88	Proton Family	f	96	\N	\N
2486	1	2025-10-20	7	287.88	Proton Family	f	96	\N	\N
2534	1	2026-03-01	4	55.00	BJ's Club Membership	f	97	\N	\N
983	1	2026-03-01	4	400.00	Children's Clothes	f	44	\N	\N
984	1	2026-09-01	4	400.00	Children's Clothes	f	44	\N	\N
985	1	2027-03-01	4	400.00	Children's Clothes	f	44	\N	\N
986	1	2027-09-01	4	400.00	Children's Clothes	f	44	\N	\N
2535	1	2026-03-15	4	55.00	BJ's Club Membership	f	97	\N	\N
2536	1	2026-03-29	4	55.00	BJ's Club Membership	f	97	\N	\N
2537	1	2026-04-12	4	55.00	BJ's Club Membership	f	97	\N	\N
2538	1	2026-04-26	4	55.00	BJ's Club Membership	f	97	\N	\N
2539	1	2026-05-10	4	55.00	BJ's Club Membership	f	97	\N	\N
2540	1	2026-05-24	4	55.00	BJ's Club Membership	f	97	\N	\N
2541	1	2026-06-07	4	55.00	BJ's Club Membership	f	97	\N	\N
2542	1	2026-06-21	4	55.00	BJ's Club Membership	f	97	\N	\N
2543	1	2026-07-05	4	55.00	BJ's Club Membership	f	97	\N	\N
2544	1	2026-07-19	4	55.00	BJ's Club Membership	f	97	\N	\N
2545	1	2026-08-02	4	55.00	BJ's Club Membership	f	97	\N	\N
2546	1	2026-08-16	4	55.00	BJ's Club Membership	f	97	\N	\N
2547	1	2026-08-30	4	55.00	BJ's Club Membership	f	97	\N	\N
2548	1	2026-09-13	4	55.00	BJ's Club Membership	f	97	\N	\N
2549	1	2026-09-27	4	55.00	BJ's Club Membership	f	97	\N	\N
2550	1	2026-10-11	4	55.00	BJ's Club Membership	f	97	\N	\N
2551	1	2026-10-25	4	55.00	BJ's Club Membership	f	97	\N	\N
2552	1	2026-11-08	4	55.00	BJ's Club Membership	f	97	\N	\N
2553	1	2026-11-22	4	55.00	BJ's Club Membership	f	97	\N	\N
2554	1	2026-12-06	4	55.00	BJ's Club Membership	f	97	\N	\N
2555	1	2026-12-20	4	55.00	BJ's Club Membership	f	97	\N	\N
2556	1	2027-01-03	4	55.00	BJ's Club Membership	f	97	\N	\N
2557	1	2027-01-17	4	55.00	BJ's Club Membership	f	97	\N	\N
2558	1	2027-01-31	4	55.00	BJ's Club Membership	f	97	\N	\N
2559	1	2027-02-14	4	55.00	BJ's Club Membership	f	97	\N	\N
2560	1	2027-02-28	4	55.00	BJ's Club Membership	f	97	\N	\N
1043	1	2025-05-08	4	40.00	Josh's Spending Money	f	46	\N	7108
1044	1	2025-05-22	4	40.00	Josh's Spending Money	f	46	\N	7109
1048	1	2025-07-17	4	40.00	Josh's Spending Money	f	46	\N	7111
1049	1	2025-07-31	4	40.00	Josh's Spending Money	f	46	\N	7111
2577	1	2025-10-30	4	100.00	Sam's Club Membership	f	98	\N	\N
2578	1	2025-11-13	4	100.00	Sam's Club Membership	f	98	\N	\N
2579	1	2025-11-27	4	100.00	Sam's Club Membership	f	98	\N	\N
2580	1	2025-12-11	4	100.00	Sam's Club Membership	f	98	\N	\N
2581	1	2025-12-25	4	100.00	Sam's Club Membership	f	98	\N	\N
2582	1	2026-01-08	4	100.00	Sam's Club Membership	f	98	\N	\N
2583	1	2026-01-22	4	100.00	Sam's Club Membership	f	98	\N	\N
2584	1	2026-02-05	4	100.00	Sam's Club Membership	f	98	\N	\N
2585	1	2026-02-19	4	100.00	Sam's Club Membership	f	98	\N	\N
2586	1	2026-03-05	4	100.00	Sam's Club Membership	f	98	\N	\N
2587	1	2026-03-19	4	100.00	Sam's Club Membership	f	98	\N	\N
2588	1	2026-04-02	4	100.00	Sam's Club Membership	f	98	\N	\N
2589	1	2026-04-16	4	100.00	Sam's Club Membership	f	98	\N	\N
2590	1	2026-04-30	4	100.00	Sam's Club Membership	f	98	\N	\N
2591	1	2026-05-14	4	100.00	Sam's Club Membership	f	98	\N	\N
2592	1	2026-05-28	4	100.00	Sam's Club Membership	f	98	\N	\N
2593	1	2026-06-11	4	100.00	Sam's Club Membership	f	98	\N	\N
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
2594	1	2026-06-25	4	100.00	Sam's Club Membership	f	98	\N	\N
2595	1	2026-07-09	4	100.00	Sam's Club Membership	f	98	\N	\N
2596	1	2026-07-23	4	100.00	Sam's Club Membership	f	98	\N	\N
2597	1	2026-08-06	4	100.00	Sam's Club Membership	f	98	\N	\N
2598	1	2026-08-20	4	100.00	Sam's Club Membership	f	98	\N	\N
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
2599	1	2026-09-03	4	100.00	Sam's Club Membership	f	98	\N	\N
2600	1	2026-09-17	4	100.00	Sam's Club Membership	f	98	\N	\N
2601	1	2026-10-01	4	100.00	Sam's Club Membership	f	98	\N	\N
2602	1	2026-10-15	4	100.00	Sam's Club Membership	f	98	\N	\N
2603	1	2026-10-29	4	100.00	Sam's Club Membership	f	98	\N	\N
2604	1	2026-11-12	4	100.00	Sam's Club Membership	f	98	\N	\N
2605	1	2026-11-26	4	100.00	Sam's Club Membership	f	98	\N	\N
2606	1	2026-12-10	4	100.00	Sam's Club Membership	f	98	\N	\N
2607	1	2026-12-24	4	100.00	Sam's Club Membership	f	98	\N	\N
2608	1	2027-01-07	4	100.00	Sam's Club Membership	f	98	\N	\N
2609	1	2027-01-21	4	100.00	Sam's Club Membership	f	98	\N	\N
2610	1	2027-02-04	4	100.00	Sam's Club Membership	f	98	\N	\N
2611	1	2027-02-18	4	100.00	Sam's Club Membership	f	98	\N	\N
2612	1	2027-03-04	4	100.00	Sam's Club Membership	f	98	\N	\N
2613	1	2027-03-18	4	100.00	Sam's Club Membership	f	98	\N	\N
2614	1	2027-04-01	4	100.00	Sam's Club Membership	f	98	\N	\N
2615	1	2027-03-11	7	1300.00	Kayla's New Phone	f	99	\N	\N
2669	1	2026-02-22	4	100.00	Knox's Birthday	f	101	\N	\N
2670	1	2026-03-08	4	100.00	Knox's Birthday	f	101	\N	\N
2671	1	2026-03-22	4	100.00	Knox's Birthday	f	101	\N	\N
2672	1	2026-04-05	4	100.00	Knox's Birthday	f	101	\N	\N
2673	1	2026-04-19	4	100.00	Knox's Birthday	f	101	\N	\N
2674	1	2026-05-03	4	100.00	Knox's Birthday	f	101	\N	\N
2675	1	2026-05-17	4	100.00	Knox's Birthday	f	101	\N	\N
2676	1	2026-05-31	4	100.00	Knox's Birthday	f	101	\N	\N
2677	1	2026-06-14	4	100.00	Knox's Birthday	f	101	\N	\N
2678	1	2026-06-28	4	100.00	Knox's Birthday	f	101	\N	\N
2679	1	2026-07-12	4	100.00	Knox's Birthday	f	101	\N	\N
2680	1	2026-07-26	4	100.00	Knox's Birthday	f	101	\N	\N
2681	1	2026-08-09	4	100.00	Knox's Birthday	f	101	\N	\N
2682	1	2026-08-23	4	100.00	Knox's Birthday	f	101	\N	\N
2683	1	2026-09-06	4	100.00	Knox's Birthday	f	101	\N	\N
2684	1	2026-09-20	4	100.00	Knox's Birthday	f	101	\N	\N
2685	1	2026-10-04	4	100.00	Knox's Birthday	f	101	\N	\N
2686	1	2026-10-18	4	100.00	Knox's Birthday	f	101	\N	\N
2687	1	2026-11-01	4	100.00	Knox's Birthday	f	101	\N	\N
2688	1	2026-11-15	4	100.00	Knox's Birthday	f	101	\N	\N
2689	1	2026-11-29	4	100.00	Knox's Birthday	f	101	\N	\N
2690	1	2026-12-13	4	100.00	Knox's Birthday	f	101	\N	\N
2691	1	2026-12-27	4	100.00	Knox's Birthday	f	101	\N	\N
2692	1	2027-01-10	4	100.00	Knox's Birthday	f	101	\N	\N
2693	1	2027-01-24	4	100.00	Knox's Birthday	f	101	\N	\N
2694	1	2027-02-07	4	100.00	Knox's Birthday	f	101	\N	\N
2695	1	2027-02-21	4	100.00	Knox's Birthday	f	101	\N	\N
2696	1	2026-02-14	4	100.00	Valentine's Day	f	102	\N	\N
2697	1	2026-02-28	4	100.00	Valentine's Day	f	102	\N	\N
2698	1	2026-03-14	4	100.00	Valentine's Day	f	102	\N	\N
2699	1	2026-03-28	4	100.00	Valentine's Day	f	102	\N	\N
2700	1	2026-04-11	4	100.00	Valentine's Day	f	102	\N	\N
2701	1	2026-04-25	4	100.00	Valentine's Day	f	102	\N	\N
2702	1	2026-05-09	4	100.00	Valentine's Day	f	102	\N	\N
2703	1	2026-05-23	4	100.00	Valentine's Day	f	102	\N	\N
2704	1	2026-06-06	4	100.00	Valentine's Day	f	102	\N	\N
2705	1	2026-06-20	4	100.00	Valentine's Day	f	102	\N	\N
2706	1	2026-07-04	4	100.00	Valentine's Day	f	102	\N	\N
2707	1	2026-07-18	4	100.00	Valentine's Day	f	102	\N	\N
2708	1	2026-08-01	4	100.00	Valentine's Day	f	102	\N	\N
2709	1	2026-08-15	4	100.00	Valentine's Day	f	102	\N	\N
2710	1	2026-08-29	4	100.00	Valentine's Day	f	102	\N	\N
2711	1	2026-09-12	4	100.00	Valentine's Day	f	102	\N	\N
2712	1	2026-09-26	4	100.00	Valentine's Day	f	102	\N	\N
2713	1	2026-10-10	4	100.00	Valentine's Day	f	102	\N	\N
2714	1	2026-10-24	4	100.00	Valentine's Day	f	102	\N	\N
2715	1	2026-11-07	4	100.00	Valentine's Day	f	102	\N	\N
2716	1	2026-11-21	4	100.00	Valentine's Day	f	102	\N	\N
2717	1	2026-12-05	4	100.00	Valentine's Day	f	102	\N	\N
2718	1	2026-12-19	4	100.00	Valentine's Day	f	102	\N	\N
2719	1	2027-01-02	4	100.00	Valentine's Day	f	102	\N	\N
2720	1	2027-01-16	4	100.00	Valentine's Day	f	102	\N	\N
2721	1	2027-01-30	4	100.00	Valentine's Day	f	102	\N	\N
2722	1	2027-02-13	4	100.00	Valentine's Day	f	102	\N	\N
2723	1	2027-02-27	4	100.00	Valentine's Day	f	102	\N	\N
2738	1	2025-10-24	4	200.00	Strawberry Picking	f	103	\N	\N
2739	1	2025-11-07	4	200.00	Strawberry Picking	f	103	\N	\N
2740	1	2025-11-21	4	200.00	Strawberry Picking	f	103	\N	\N
2741	1	2025-12-05	4	200.00	Strawberry Picking	f	103	\N	\N
2742	1	2025-12-19	4	200.00	Strawberry Picking	f	103	\N	\N
2743	1	2026-01-02	4	200.00	Strawberry Picking	f	103	\N	\N
2744	1	2026-01-16	4	200.00	Strawberry Picking	f	103	\N	\N
2745	1	2026-01-30	4	200.00	Strawberry Picking	f	103	\N	\N
2746	1	2026-02-13	4	200.00	Strawberry Picking	f	103	\N	\N
2747	1	2026-02-27	4	200.00	Strawberry Picking	f	103	\N	\N
2748	1	2026-03-13	4	200.00	Strawberry Picking	f	103	\N	\N
2749	1	2026-03-27	4	200.00	Strawberry Picking	f	103	\N	\N
2750	1	2026-04-10	4	200.00	Strawberry Picking	f	103	\N	\N
2751	1	2026-04-24	4	200.00	Strawberry Picking	f	103	\N	\N
2752	1	2026-05-08	4	200.00	Strawberry Picking	f	103	\N	\N
2753	1	2026-05-22	4	200.00	Strawberry Picking	f	103	\N	\N
2754	1	2026-06-05	4	200.00	Strawberry Picking	f	103	\N	\N
2755	1	2026-06-19	4	200.00	Strawberry Picking	f	103	\N	\N
2756	1	2026-07-03	4	200.00	Strawberry Picking	f	103	\N	\N
2757	1	2026-07-17	4	200.00	Strawberry Picking	f	103	\N	\N
2758	1	2026-07-31	4	200.00	Strawberry Picking	f	103	\N	\N
2759	1	2026-08-14	4	200.00	Strawberry Picking	f	103	\N	\N
2760	1	2026-08-28	4	200.00	Strawberry Picking	f	103	\N	\N
2761	1	2026-09-11	4	200.00	Strawberry Picking	f	103	\N	\N
2762	1	2026-09-25	4	200.00	Strawberry Picking	f	103	\N	\N
2763	1	2026-10-09	4	200.00	Strawberry Picking	f	103	\N	\N
2764	1	2026-10-23	4	200.00	Strawberry Picking	f	103	\N	\N
2765	1	2026-11-06	4	200.00	Strawberry Picking	f	103	\N	\N
2766	1	2026-11-20	4	200.00	Strawberry Picking	f	103	\N	\N
2767	1	2026-12-04	4	200.00	Strawberry Picking	f	103	\N	\N
2768	1	2026-12-18	4	200.00	Strawberry Picking	f	103	\N	\N
2769	1	2027-01-01	4	200.00	Strawberry Picking	f	103	\N	\N
2770	1	2027-01-15	4	200.00	Strawberry Picking	f	103	\N	\N
2771	1	2027-01-29	4	200.00	Strawberry Picking	f	103	\N	\N
2772	1	2027-02-12	4	200.00	Strawberry Picking	f	103	\N	\N
2773	1	2027-02-26	4	200.00	Strawberry Picking	f	103	\N	\N
2774	1	2027-03-12	4	200.00	Strawberry Picking	f	103	\N	\N
2775	1	2027-03-26	4	200.00	Strawberry Picking	f	103	\N	\N
2776	1	2027-04-09	4	200.00	Strawberry Picking	f	103	\N	\N
2073	1	2025-05-01	4	100.00	Kayla's Birthday	f	87	\N	7107
2724	1	2025-04-11	4	200.00	Strawberry Picking	f	103	\N	7106
2725	1	2025-04-25	4	200.00	Strawberry Picking	f	103	\N	7107
2726	1	2025-05-09	4	200.00	Strawberry Picking	f	103	\N	7108
2561	1	2025-03-11	4	55.00	Sam's Club Membership	f	98		7104
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
2292	1	2025-05-11	4	100.00	Mother's Day	f	92	\N	7108
2293	1	2025-05-25	4	100.00	Mother's Day	f	92	\N	7109
2294	1	2025-06-08	4	100.00	Mother's Day	f	92	\N	7110
2346	1	2025-06-11	4	100.00	Father's Day	f	93	\N	7110
2565	1	2025-05-15	4	100.00	Sam's Club Membership	f	98	\N	7108
2566	1	2025-05-29	4	100.00	Sam's Club Membership	f	98	\N	7109
2567	1	2025-06-12	4	100.00	Sam's Club Membership	f	98	\N	7110
2727	1	2025-05-23	4	200.00	Strawberry Picking	f	103	\N	7109
2728	1	2025-06-06	4	200.00	Strawberry Picking	f	103	\N	7110
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
2295	1	2025-06-22	4	100.00	Mother's Day	f	92	\N	7111
2347	1	2025-06-25	4	100.00	Father's Day	f	93	\N	7111
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
2076	1	2025-08-22	4	100.00	New Baby Birthday	f	88	\N	\N
2077	1	2025-09-05	4	100.00	New Baby Birthday	f	88	\N	\N
2078	1	2025-09-19	4	100.00	New Baby Birthday	f	88	\N	\N
2079	1	2025-10-03	4	100.00	New Baby Birthday	f	88	\N	\N
2080	1	2025-10-17	4	100.00	New Baby Birthday	f	88	\N	\N
2129	1	2025-10-07	4	100.00	Ariella's Birthday	f	89	\N	\N
2240	1	2025-08-12	4	600.00	School Curriculum	f	91	\N	\N
2241	1	2025-08-26	4	600.00	School Curriculum	f	91	\N	\N
2242	1	2025-09-09	4	600.00	School Curriculum	f	91	\N	\N
2243	1	2025-09-23	4	600.00	School Curriculum	f	91	\N	\N
2244	1	2025-10-07	4	600.00	School Curriculum	f	91	\N	\N
2298	1	2025-08-03	4	100.00	Mother's Day	f	92	\N	\N
2299	1	2025-08-17	4	100.00	Mother's Day	f	92	\N	\N
2300	1	2025-08-31	4	100.00	Mother's Day	f	92	\N	\N
2301	1	2025-09-14	4	100.00	Mother's Day	f	92	\N	\N
2302	1	2025-09-28	4	100.00	Mother's Day	f	92	\N	\N
2303	1	2025-10-12	4	100.00	Mother's Day	f	92	\N	\N
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
2350	1	2025-08-06	4	100.00	Father's Day	f	93	\N	\N
2351	1	2025-08-20	4	100.00	Father's Day	f	93	\N	\N
2352	1	2025-09-03	4	100.00	Father's Day	f	93	\N	\N
1662	1	2025-07-17	6	30.00	Toilet Paper	f	63	\N	7111
2237	1	2025-07-01	4	600.00	School Curriculum	f	91	\N	7111
2238	1	2025-07-15	4	600.00	School Curriculum	f	91	\N	7111
2239	1	2025-07-29	4	600.00	School Curriculum	f	91	\N	7111
1685	1	2025-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	7109
2568	1	2025-06-26	4	100.00	Sam's Club Membership	f	98	\N	7111
1687	1	2025-12-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1688	1	2026-03-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1689	1	2026-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1690	1	2026-09-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1691	1	2026-12-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1692	1	2027-03-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1693	1	2027-06-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
1686	1	2025-09-01	6	61.50	Anchor Trash Pickup	f	64	\N	\N
2353	1	2025-09-17	4	100.00	Father's Day	f	93	\N	\N
2354	1	2025-10-01	4	100.00	Father's Day	f	93	\N	\N
2355	1	2025-10-15	4	100.00	Father's Day	f	93	\N	\N
2400	1	2025-10-18	4	100.00	Wedding Anniversary	f	94	\N	\N
2571	1	2025-08-07	4	100.00	Sam's Club Membership	f	98	\N	\N
2572	1	2025-08-21	4	100.00	Sam's Club Membership	f	98	\N	\N
2573	1	2025-09-04	4	100.00	Sam's Club Membership	f	98	\N	\N
2574	1	2025-09-18	4	100.00	Sam's Club Membership	f	98	\N	\N
2575	1	2025-10-02	4	100.00	Sam's Club Membership	f	98	\N	\N
2576	1	2025-10-16	4	100.00	Sam's Club Membership	f	98	\N	\N
2732	1	2025-08-01	4	200.00	Strawberry Picking	f	103	\N	\N
2733	1	2025-08-15	4	200.00	Strawberry Picking	f	103	\N	\N
2734	1	2025-08-29	4	200.00	Strawberry Picking	f	103	\N	\N
2735	1	2025-09-12	4	200.00	Strawberry Picking	f	103	\N	\N
2736	1	2025-09-26	4	200.00	Strawberry Picking	f	103	\N	\N
2737	1	2025-10-10	4	200.00	Strawberry Picking	f	103	\N	\N
2729	1	2025-06-20	4	200.00	Strawberry Picking	f	103	\N	7111
2569	1	2025-07-10	4	100.00	Sam's Club Membership	f	98	\N	7111
2570	1	2025-07-24	4	100.00	Sam's Club Membership	f	98	\N	7111
2730	1	2025-07-04	4	200.00	Strawberry Picking	f	103	\N	7111
2731	1	2025-07-18	4	200.00	Strawberry Picking	f	103	\N	7111
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
-- TOC entry 3553 (class 0 OID 16419)
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
-- TOC entry 3555 (class 0 OID 16430)
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
-- TOC entry 3573 (class 0 OID 16573)
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
-- TOC entry 3567 (class 0 OID 16515)
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
-- TOC entry 3563 (class 0 OID 16481)
-- Dependencies: 234
-- Data for Name: recurring_schedules; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.recurring_schedules (id, user_id, type_id, description, frequency_id, "interval", start_date, end_date, amount, category_type, category_id, default_account_id) FROM stdin;
108	1	2	Emergency Fund	1	1	2025-03-13	2027-04-01	500.00	expense	8	1
117	1	1	State Tax Return	\N	1	2025-03-13	2025-03-13	895.00	\N	\N	\N
113	1	1	Salary - $87,685.00/year (Combined) (Combined) (Combined) (Combined) (Combined)	\N	1	2025-03-13	2025-06-30	3372.50	\N	\N	\N
114	1	2	Josh's Birthday	3	1	2025-03-24	2027-03-31	100.00	expense	4	1
116	1	1	Salary - $87,685.00/year	\N	1	2025-03-13	2025-06-30	3372.50	\N	\N	\N
67	1	1	State Tax Return (Combined) (Combined) (Combined) (Combined)	\N	1	2025-03-14	2025-03-14	895.00	\N	\N	\N
13	1	1	Salary - 87685.00/year	1	1	2025-02-27	2025-06-30	3372.50	income	\N	\N
22	1	2	Mortgage	2	1	2025-03-01	2025-12-31	1670.20	expense	6	1
21	1	2	Gas	1	1	2025-03-13	2027-05-01	80.00	expense	1	1
23	1	2	Groceries	1	1	2025-03-13	2027-03-31	400.00	expense	5	1
24	1	2	Apple Music	2	1	2025-03-15	2027-03-31	18.14	expense	3	1
25	1	2	Van Payment	2	1	2025-03-20	2029-03-01	531.94	expense	1	1
26	1	2	Geico Car Insurance	2	1	2025-04-01	2027-04-01	183.49	expense	1	1
28	1	2	Oil & Air Filters	2	6	2025-04-11	2027-04-20	175.00	expense	1	1
68	1	1	Federal Tax Return	\N	1	2026-03-08	2026-03-08	3142.00	\N	\N	\N
34	1	2	Audible	2	1	2025-03-26	2027-04-08	15.96	expense	3	1
35	1	2	Disney+	2	1	2025-03-26	2027-04-08	17.15	expense	3	1
36	1	2	iCloud 2TB	2	1	2025-03-17	2027-04-08	9.99	expense	7	1
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
88	1	2	New Baby Birthday	3	1	2025-08-22	2027-08-24	100.00	expense	4	1
89	1	2	Ariella's Birthday	3	1	2025-10-07	2027-10-08	100.00	expense	4	1
90	1	2	Christmas	3	1	2025-11-01	2027-12-01	400.00	expense	4	1
91	1	2	School Curriculum	3	1	2025-07-01	2027-08-01	600.00	expense	4	1
92	1	2	Mother's Day	3	1	2025-05-11	2027-06-01	100.00	expense	4	1
93	1	2	Father's Day	3	1	2025-06-11	2027-07-01	100.00	expense	4	1
94	1	2	Wedding Anniversary	3	1	2025-10-18	2027-11-01	100.00	expense	4	1
95	1	2	Josh's New Phone	3	2	2025-11-11	2027-12-01	1300.00	expense	7	1
96	1	2	Proton Family	3	1	2025-08-11	2027-08-12	287.88	expense	7	1
97	1	2	BJ's Club Membership	3	1	2026-03-01	2027-03-02	55.00	expense	4	1
98	1	2	Sam's Club Membership	3	1	2025-03-20	2027-04-01	100.00	expense	4	1
99	1	2	Kayla's New Phone	3	2	2027-03-11	2027-04-01	1300.00	expense	7	1
101	1	2	Knox's Birthday	3	1	2026-02-22	2027-03-01	100.00	expense	4	1
102	1	2	Valentine's Day	3	1	2026-02-14	2027-03-01	100.00	expense	4	1
103	1	2	Strawberry Picking	3	1	2025-04-11	2027-04-12	200.00	expense	4	1
\.


--
-- TOC entry 3547 (class 0 OID 16390)
-- Dependencies: 218
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.roles (id, name, description) FROM stdin;
1	ADMIN	Administrator role
\.


--
-- TOC entry 3565 (class 0 OID 16503)
-- Dependencies: 236
-- Data for Name: salary_changes; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.salary_changes (id, user_id, effective_date, end_date, gross_annual_salary, federal_tax_rate, state_tax_rate, retirement_contribution_rate, health_insurance_amount, other_deductions_amount, notes) FROM stdin;
17	1	2025-03-13	2025-06-30	87685.00	4.00	1.40	6.00	249.00	370.00	
\.


--
-- TOC entry 3581 (class 0 OID 16639)
-- Dependencies: 252
-- Data for Name: salary_deposit_allocations; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.salary_deposit_allocations (id, salary_id, account_id, is_percentage, percentage, amount) FROM stdin;
16	17	1	t	100.00	\N
\.


--
-- TOC entry 3551 (class 0 OID 16408)
-- Dependencies: 222
-- Data for Name: schedule_types; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.schedule_types (id, name, description) FROM stdin;
1	income	Regular income
2	expense	Expense
\.


--
-- TOC entry 3577 (class 0 OID 16608)
-- Dependencies: 248
-- Data for Name: transactions; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.transactions (id, account_id, transaction_date, amount, description, transaction_type, related_transaction_id) FROM stdin;
6	4	2025-03-01	0.13	Interest accrual (20.00% daily)	deposit	\N
\.


--
-- TOC entry 3559 (class 0 OID 16448)
-- Dependencies: 230
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: grubb
--

COPY public.users (id, username, password_hash, email, role_id, first_name, last_name) FROM stdin;
1	josh	scrypt:32768:8:1$WP5YzwEHpheGD7Ox$4200d87d5da1418a09789ee0a9629f24ff504602c0558d0c6d25eda5b3ce6072a5e3cdb041e666b6edaf7f96e8fd59b5249e969ef8e7472570a0f0761d8fc756	grubbj@pm.me	1	Josh	Grubb
\.


--
-- TOC entry 3615 (class 0 OID 0)
-- Dependencies: 249
-- Name: account_interest_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.account_interest_id_seq', 3, true);


--
-- TOC entry 3616 (class 0 OID 0)
-- Dependencies: 219
-- Name: account_types_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.account_types_id_seq', 17, true);


--
-- TOC entry 3617 (class 0 OID 0)
-- Dependencies: 231
-- Name: accounts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.accounts_id_seq', 7, true);


--
-- TOC entry 3618 (class 0 OID 0)
-- Dependencies: 227
-- Name: expense_categories_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_categories_id_seq', 8, true);


--
-- TOC entry 3619 (class 0 OID 0)
-- Dependencies: 241
-- Name: expense_changes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_changes_id_seq', 1, false);


--
-- TOC entry 3620 (class 0 OID 0)
-- Dependencies: 245
-- Name: expense_payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expense_payments_id_seq', 4, true);


--
-- TOC entry 3621 (class 0 OID 0)
-- Dependencies: 239
-- Name: expenses_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.expenses_id_seq', 2904, true);


--
-- TOC entry 3622 (class 0 OID 0)
-- Dependencies: 223
-- Name: frequencies_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.frequencies_id_seq', 7, true);


--
-- TOC entry 3623 (class 0 OID 0)
-- Dependencies: 225
-- Name: income_categories_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.income_categories_id_seq', 6, true);


--
-- TOC entry 3624 (class 0 OID 0)
-- Dependencies: 243
-- Name: income_payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.income_payments_id_seq', 7093, true);


--
-- TOC entry 3625 (class 0 OID 0)
-- Dependencies: 237
-- Name: paychecks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.paychecks_id_seq', 7112, true);


--
-- TOC entry 3626 (class 0 OID 0)
-- Dependencies: 233
-- Name: recurring_schedules_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.recurring_schedules_id_seq', 121, true);


--
-- TOC entry 3627 (class 0 OID 0)
-- Dependencies: 217
-- Name: roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.roles_id_seq', 1, true);


--
-- TOC entry 3628 (class 0 OID 0)
-- Dependencies: 235
-- Name: salary_changes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.salary_changes_id_seq', 17, true);


--
-- TOC entry 3629 (class 0 OID 0)
-- Dependencies: 251
-- Name: salary_deposit_allocations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.salary_deposit_allocations_id_seq', 16, true);


--
-- TOC entry 3630 (class 0 OID 0)
-- Dependencies: 221
-- Name: schedule_types_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.schedule_types_id_seq', 2, true);


--
-- TOC entry 3631 (class 0 OID 0)
-- Dependencies: 247
-- Name: transactions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.transactions_id_seq', 12, true);


--
-- TOC entry 3632 (class 0 OID 0)
-- Dependencies: 229
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: grubb
--

SELECT pg_catalog.setval('public.users_id_seq', 1, true);


--
-- TOC entry 3371 (class 2606 OID 16630)
-- Name: account_interest account_interest_account_id_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_account_id_key UNIQUE (account_id);


--
-- TOC entry 3373 (class 2606 OID 16628)
-- Name: account_interest account_interest_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_pkey PRIMARY KEY (id);


--
-- TOC entry 3332 (class 2606 OID 16406)
-- Name: account_types account_types_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_types
    ADD CONSTRAINT account_types_pkey PRIMARY KEY (id);


--
-- TOC entry 3350 (class 2606 OID 16469)
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- TOC entry 3344 (class 2606 OID 16446)
-- Name: expense_categories expense_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_categories
    ADD CONSTRAINT expense_categories_pkey PRIMARY KEY (id);


--
-- TOC entry 3363 (class 2606 OID 16566)
-- Name: expense_changes expense_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes
    ADD CONSTRAINT expense_changes_pkey PRIMARY KEY (id);


--
-- TOC entry 3367 (class 2606 OID 16595)
-- Name: expense_payments expense_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_pkey PRIMARY KEY (id);


--
-- TOC entry 3360 (class 2606 OID 16544)
-- Name: expenses expenses_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_pkey PRIMARY KEY (id);


--
-- TOC entry 3338 (class 2606 OID 16428)
-- Name: frequencies frequencies_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies
    ADD CONSTRAINT frequencies_name_key UNIQUE (name);


--
-- TOC entry 3340 (class 2606 OID 16426)
-- Name: frequencies frequencies_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.frequencies
    ADD CONSTRAINT frequencies_pkey PRIMARY KEY (id);


--
-- TOC entry 3342 (class 2606 OID 16437)
-- Name: income_categories income_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_categories
    ADD CONSTRAINT income_categories_pkey PRIMARY KEY (id);


--
-- TOC entry 3365 (class 2606 OID 16578)
-- Name: income_payments income_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_pkey PRIMARY KEY (id);


--
-- TOC entry 3358 (class 2606 OID 16520)
-- Name: paychecks paychecks_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_pkey PRIMARY KEY (id);


--
-- TOC entry 3354 (class 2606 OID 16486)
-- Name: recurring_schedules recurring_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_pkey PRIMARY KEY (id);


--
-- TOC entry 3328 (class 2606 OID 16399)
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- TOC entry 3330 (class 2606 OID 16397)
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- TOC entry 3356 (class 2606 OID 16508)
-- Name: salary_changes salary_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes
    ADD CONSTRAINT salary_changes_pkey PRIMARY KEY (id);


--
-- TOC entry 3376 (class 2606 OID 16644)
-- Name: salary_deposit_allocations salary_deposit_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_pkey PRIMARY KEY (id);


--
-- TOC entry 3334 (class 2606 OID 16417)
-- Name: schedule_types schedule_types_name_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types
    ADD CONSTRAINT schedule_types_name_key UNIQUE (name);


--
-- TOC entry 3336 (class 2606 OID 16415)
-- Name: schedule_types schedule_types_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.schedule_types
    ADD CONSTRAINT schedule_types_pkey PRIMARY KEY (id);


--
-- TOC entry 3369 (class 2606 OID 16613)
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- TOC entry 3346 (class 2606 OID 16455)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 3348 (class 2606 OID 16457)
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- TOC entry 3374 (class 1259 OID 16636)
-- Name: idx_account_interest_account_id; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_account_interest_account_id ON public.account_interest USING btree (account_id);


--
-- TOC entry 3361 (class 1259 OID 16672)
-- Name: idx_expenses_paycheck_id; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_expenses_paycheck_id ON public.expenses USING btree (paycheck_id);


--
-- TOC entry 3351 (class 1259 OID 16661)
-- Name: idx_recurring_schedules_account; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_recurring_schedules_account ON public.recurring_schedules USING btree (default_account_id);


--
-- TOC entry 3352 (class 1259 OID 16660)
-- Name: idx_recurring_schedules_category; Type: INDEX; Schema: public; Owner: grubb
--

CREATE INDEX idx_recurring_schedules_category ON public.recurring_schedules USING btree (category_type, category_id);


--
-- TOC entry 3398 (class 2606 OID 16631)
-- Name: account_interest account_interest_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.account_interest
    ADD CONSTRAINT account_interest_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- TOC entry 3378 (class 2606 OID 16475)
-- Name: accounts accounts_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.account_types(id);


--
-- TOC entry 3379 (class 2606 OID 16470)
-- Name: accounts accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3392 (class 2606 OID 16567)
-- Name: expense_changes expense_changes_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_changes
    ADD CONSTRAINT expense_changes_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3395 (class 2606 OID 16601)
-- Name: expense_payments expense_payments_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3396 (class 2606 OID 16596)
-- Name: expense_payments expense_payments_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expense_payments
    ADD CONSTRAINT expense_payments_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id);


--
-- TOC entry 3388 (class 2606 OID 16550)
-- Name: expenses expenses_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.expense_categories(id);


--
-- TOC entry 3389 (class 2606 OID 16555)
-- Name: expenses expenses_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3390 (class 2606 OID 16545)
-- Name: expenses expenses_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3391 (class 2606 OID 16667)
-- Name: expenses fk_expenses_paycheck; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT fk_expenses_paycheck FOREIGN KEY (paycheck_id) REFERENCES public.paychecks(id) ON DELETE SET NULL;


--
-- TOC entry 3393 (class 2606 OID 16584)
-- Name: income_payments income_payments_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3394 (class 2606 OID 16579)
-- Name: income_payments income_payments_paycheck_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.income_payments
    ADD CONSTRAINT income_payments_paycheck_id_fkey FOREIGN KEY (paycheck_id) REFERENCES public.paychecks(id);


--
-- TOC entry 3385 (class 2606 OID 16526)
-- Name: paychecks paychecks_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.income_categories(id);


--
-- TOC entry 3386 (class 2606 OID 16531)
-- Name: paychecks paychecks_recurring_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_recurring_schedule_id_fkey FOREIGN KEY (recurring_schedule_id) REFERENCES public.recurring_schedules(id);


--
-- TOC entry 3387 (class 2606 OID 16521)
-- Name: paychecks paychecks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.paychecks
    ADD CONSTRAINT paychecks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3380 (class 2606 OID 16655)
-- Name: recurring_schedules recurring_schedules_default_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_default_account_id_fkey FOREIGN KEY (default_account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3381 (class 2606 OID 16497)
-- Name: recurring_schedules recurring_schedules_frequency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_frequency_id_fkey FOREIGN KEY (frequency_id) REFERENCES public.frequencies(id);


--
-- TOC entry 3382 (class 2606 OID 16492)
-- Name: recurring_schedules recurring_schedules_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.schedule_types(id);


--
-- TOC entry 3383 (class 2606 OID 16487)
-- Name: recurring_schedules recurring_schedules_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.recurring_schedules
    ADD CONSTRAINT recurring_schedules_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3384 (class 2606 OID 16509)
-- Name: salary_changes salary_changes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_changes
    ADD CONSTRAINT salary_changes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3399 (class 2606 OID 16650)
-- Name: salary_deposit_allocations salary_deposit_allocations_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3400 (class 2606 OID 16645)
-- Name: salary_deposit_allocations salary_deposit_allocations_salary_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.salary_deposit_allocations
    ADD CONSTRAINT salary_deposit_allocations_salary_id_fkey FOREIGN KEY (salary_id) REFERENCES public.salary_changes(id);


--
-- TOC entry 3397 (class 2606 OID 16614)
-- Name: transactions transactions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- TOC entry 3377 (class 2606 OID 16458)
-- Name: users users_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: grubb
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id);


-- Completed on 2025-03-12 20:39:11 EDT

--
-- PostgreSQL database dump complete
--

-- Completed on 2025-03-12 20:39:11 EDT

--
-- PostgreSQL database cluster dump complete
--

