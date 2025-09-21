#include <string.h>
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "lwip/err.h"
#include "lwip/sys.h"

#include "sdkconfig.h"

char Self_IP[20];


#define NR_OF_IP_ADDRESSES_TO_WAIT_FOR (s_active_interfaces)

static int s_active_interfaces = 0;
static xSemaphoreHandle s_semph_get_ip_addrs;
static esp_netif_t *s_esp_netif = NULL;

static char *TAG = "net";

static esp_netif_t *eth_start(void);
static void eth_stop(void);

static bool is_our_netif(const char *prefix, esp_netif_t *netif)
{
    return strncmp(prefix, esp_netif_get_desc(netif), strlen(prefix) - 1) == 0;
}

static void start(void)
{
    s_esp_netif = eth_start();
    s_active_interfaces++;
    s_semph_get_ip_addrs = xSemaphoreCreateCounting(NR_OF_IP_ADDRESSES_TO_WAIT_FOR, 0);
}

static void stop(void)
{
    eth_stop();
    s_active_interfaces--;
}

static esp_ip4_addr_t s_ip_addr;

static void on_got_ip(void *arg, esp_event_base_t event_base,
		      int32_t event_id, void *event_data)
{
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
    if (!is_our_netif(TAG, event->esp_netif)) {
	ESP_LOGW(TAG, "Got IPv4 from another interface \"%s\": ignored", esp_netif_get_desc(event->esp_netif));
	return;
    }
    ESP_LOGI(TAG, "Got IPv4 event: Interface \"%s\" address: " IPSTR, esp_netif_get_desc(event->esp_netif), IP2STR(&event->ip_info.ip));
    memcpy(&s_ip_addr, &event->ip_info.ip, sizeof(s_ip_addr));
    xSemaphoreGive(s_semph_get_ip_addrs);
}

esp_err_t net_connect(void)
{
    if (s_semph_get_ip_addrs != NULL) {
	return ESP_ERR_INVALID_STATE;
    }

    start();
    ESP_ERROR_CHECK(esp_register_shutdown_handler(&stop));
    ESP_LOGI(TAG, "Waiting for IP(s)");
    for (int i = 0; i < NR_OF_IP_ADDRESSES_TO_WAIT_FOR; ++i) {
	xSemaphoreTake(s_semph_get_ip_addrs, portMAX_DELAY);
    }

    // iterate over active interfaces, and print out IPs of "our" netifs
    esp_netif_t *netif = NULL;
    esp_netif_ip_info_t ip;
    for (int i = 0; i < esp_netif_get_nr_of_ifs(); ++i) {
	netif = esp_netif_next(netif);
	if (is_our_netif(TAG, netif)) {
	    ESP_LOGI(TAG, "Connected to %s", esp_netif_get_desc(netif));
	    ESP_ERROR_CHECK(esp_netif_get_ip_info(netif, &ip));
	    ESP_LOGI(TAG, "- IPv4 address: " IPSTR, IP2STR(&ip.ip));
        sprintf(Self_IP,IPSTR, IP2STR(&ip.ip));
        
	}
    }


    return ESP_OK;
}

esp_err_t net_disconnect(void)
{
    if (s_semph_get_ip_addrs == NULL) {
	return ESP_ERR_INVALID_STATE;
    }
    vSemaphoreDelete(s_semph_get_ip_addrs);
    s_semph_get_ip_addrs = NULL;
    stop();
    ESP_ERROR_CHECK(esp_unregister_shutdown_handler(&stop));
    return ESP_OK;
}

static esp_eth_handle_t s_eth_handle = NULL;
static esp_eth_mac_t *s_mac = NULL;
static esp_eth_phy_t *s_phy = NULL;
static esp_eth_netif_glue_handle_t s_eth_glue = NULL;

#define	PIN_PHY_POWER 12

static esp_netif_t *eth_start(void)
{
    char *desc;
    esp_netif_inherent_config_t esp_netif_config = ESP_NETIF_INHERENT_DEFAULT_ETH();
    // Prefix the interface description with the module TAG
    // Warning: the interface desc is used in tests to capture actual connection details (IP, gw, mask)
    asprintf(&desc, "%s: %s", TAG, esp_netif_config.if_desc);
    esp_netif_config.if_desc = desc;
    esp_netif_config.route_prio = 64;
    esp_netif_config_t netif_config = {
	.base = &esp_netif_config,
	.stack = ESP_NETIF_NETSTACK_DEFAULT_ETH
    };
    esp_netif_t *netif = esp_netif_new(&netif_config);
    assert(netif);
    free(desc);

    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    gpio_pad_select_gpio(PIN_PHY_POWER);
    gpio_set_direction(PIN_PHY_POWER,GPIO_MODE_OUTPUT);
    gpio_set_level(PIN_PHY_POWER, 1);
    phy_config.phy_addr = CONFIG_ETH_PHY_ADDR;
    phy_config.reset_gpio_num = CONFIG_ETH_PHY_RST_GPIO;
    mac_config.smi_mdc_gpio_num = CONFIG_ETH_MDC_GPIO;
    mac_config.smi_mdio_gpio_num = CONFIG_ETH_MDIO_GPIO;
    s_mac = esp_eth_mac_new_esp32(&mac_config);
    s_phy = esp_eth_phy_new_lan87xx(&phy_config);

    // Install Ethernet driver
    esp_eth_config_t config = ETH_DEFAULT_CONFIG(s_mac, s_phy);
    ESP_ERROR_CHECK(esp_eth_driver_install(&config, &s_eth_handle));

    // combine driver with netif
    s_eth_glue = esp_eth_new_netif_glue(s_eth_handle);
    esp_netif_attach(netif, s_eth_glue);

    // Register user defined event handers
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, &on_got_ip, NULL));
    esp_eth_start(s_eth_handle);
    return netif;
}


esp_netif_t *get_netif_from_desc(const char *desc)
{
    esp_netif_t *netif = NULL;
    char *expected_desc;
    asprintf(&expected_desc, "%s: %s", TAG, desc);
    while ((netif = esp_netif_next(netif)) != NULL) {
	if (strcmp(esp_netif_get_desc(netif), expected_desc) == 0) {
	    free(expected_desc);
	    return netif;
	}
    }
    free(expected_desc);
    return netif;
}

static void eth_stop(void)
{
    esp_netif_t *eth_netif = get_netif_from_desc("eth");
    ESP_ERROR_CHECK(esp_event_handler_unregister(IP_EVENT, IP_EVENT_ETH_GOT_IP, &on_got_ip));
    ESP_ERROR_CHECK(esp_eth_stop(s_eth_handle));
    ESP_ERROR_CHECK(esp_eth_del_netif_glue(s_eth_glue));
    ESP_ERROR_CHECK(esp_eth_driver_uninstall(s_eth_handle));
    ESP_ERROR_CHECK(s_phy->del(s_phy));
    ESP_ERROR_CHECK(s_mac->del(s_mac));
    esp_netif_destroy(eth_netif);
    s_esp_netif = NULL;
}
