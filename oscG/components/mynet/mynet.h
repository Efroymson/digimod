#pragma once
#ifdef __cplusplus
extern "C" {
#endif

extern esp_netif_t* s_netif;

esp_err_t net_connect(void);
esp_err_t net_disconnect(void);

#ifdef __cplusplus
}
#endif
